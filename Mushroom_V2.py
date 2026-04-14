#!/usr/bin/env python3
#HAZ Mushroom Script V2 // 05/10/25 - FIXED CAMERA VERSION + MANUAL OVERRIDE + LIVE CONFIG RELOAD

##IMPORT MAIN LIBS
from config import *
import asyncio
import random
import collections
import threading
import traceback
import cv2
import time
import signal
import sys
import logging
import os
import csv
import shutil
import importlib
from datetime import datetime, timedelta
from flask import Flask, render_template, Response, jsonify, make_response
from logging.handlers import MemoryHandler

#IMPORT PIN CONTROL LIBS
import board
import busio
import adafruit_scd4x
I2C_AVAILABLE = True
import RPi.GPIO as GPIO

#BUZZER DEF
BUZZER_PIN = 5
GPIO.setmode(GPIO.BCM)
GPIO.setup(BUZZER_PIN, GPIO.OUT)
GPIO.output(BUZZER_PIN, GPIO.LOW)
buzzer_pwm = GPIO.PWM(BUZZER_PIN, 1) 
buzzer_pwm_started = False


#THREAD LOGS
log_entries = collections.deque(maxlen=200) #200 LOGS 
log_lock = threading.Lock()


#LOG HANDLER 
class InMemoryLogHandler(logging.Handler):
    def emit(self, record):
        log_entry = self.format(record)
        with log_lock:
            log_entries.append(log_entry)

#TAPO ERROR CHECKED IMPORT
try:
    from tapo import ApiClient
    TAPO_AVAILABLE = True
except Exception:
    TAPO_AVAILABLE = False
    print("Tapo library not available")    

#LOCK CSV'S
CSV_LOCK = threading.Lock()
LOG_ON_STARTUP = True

#MOVING AVERAGE WINDOW
WINDOW_SIZE_TICKS = max(1, MOVING_AVERAGE_WINDOW // CHECK_INTERVAL)

memory_handler = InMemoryLogHandler()
memory_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

#LOG SETUP
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("sensor_app")
logger.addHandler(memory_handler)

#FLASK APP
app = Flask(__name__)

#SENSOR DEF
state = {
    "sensor1": 0.0,
    "sensor2": 0.0,
    "sensor3": 0.0,
    "device1": False,
    "device2": False,
    "device1_mode": getattr(sys.modules.get('config'), 'DEVICE_1_MODE', 'auto'),
    "device2_mode": getattr(sys.modules.get('config'), 'DEVICE_2_MODE', 'auto')
}


#SHUTDOWN EVENT
shutdown_event = threading.Event()

#buzzer error function
def buzz_error(frequency=1000, duration=0.2, repeats=10, pause=0.1):
    """Buzz the PWM buzzer multiple times for errors."""
    def _buzz():
        global buzzer_pwm_started
        try:
            if not buzzer_pwm_started:
                buzzer_pwm.start(50)  # 50% duty cycle
                buzzer_pwm_started = True
            for _ in range(repeats):
                buzzer_pwm.ChangeFrequency(frequency)
                buzzer_pwm.ChangeDutyCycle(50)  
                time.sleep(duration)
                buzzer_pwm.ChangeDutyCycle(0) 
                time.sleep(pause)
        except Exception:
            pass  # don't crash
    threading.Thread(target=_buzz, daemon=True).start()

#I2CSENSOR FUNCTION
class I2CSensor:
    def __init__(self):
        if I2C_AVAILABLE:
            try:
                i2c = busio.I2C(board.SCL, board.SDA)
                self.sensor = adafruit_scd4x.SCD4X(i2c)
                self.sensor.start_periodic_measurement()
                logger.info("I2C SCD40 sensor initialized.")
            except Exception:
                self.sensor = None
                logger.exception("Failed to start I2C sensor.")
                buzz_error(frequency=1000, duration=1.0)
        else:
            self.sensor = None
            logger.warning("I2C libraries not available, using fallback simulation.")
    
    def get_average(self): #AVERAGE SENSOR DATA
        try:
            if self.sensor is not None:
                co2 = self.sensor.CO2 or 0.0
                temp = self.sensor.temperature or 0.0
                hum = self.sensor.relative_humidity or 0.0
                return hum, co2, temp
            else:
                # RANDOM VALUE FOR TESTING
                return random.uniform(50, 120), random.uniform(800, 1200), random.uniform(20, 21)
        except Exception:
            logger.exception("Error reading I2C sensor")
            buzz_error(frequency=1000, duration=1.0)
            return 0.0, 0.0, 0.0
        
#DEVICE CONTROLLER 
class DeviceController:
    def __init__(self, email, password, device1_ip, device2_ip):
        self._auto_off_tasks = set()
        self.email = email
        self.password = password
        self.device1_ip = device1_ip
        self.device2_ip = device2_ip

        self.device1_on = False
        self.device2_on = False
        self.device1_last_on = 0
        self.device2_last_on = 0
        self.cooldown_seconds = DEVICE_COOLDOWN_SECONDS
        self.max_on_duration = DEVICE_MAX_ON_DURATION
        
        # Load manual override settings
        self.device1_mode = getattr(sys.modules.get('config'), 'DEVICE_1_MODE', 'auto')
        self.device2_mode = getattr(sys.modules.get('config'), 'DEVICE_2_MODE', 'auto')
        
        logger.info(f"Device 1 Mode: {self.device1_mode}")
        logger.info(f"Device 2 Mode: {self.device2_mode}")

        try:
            self.client = ApiClient(self.email, self.password)
        except Exception:
            self.client = None

        self.device1 = None
        self.device2 = None

    async def connect(self):
        try:
            if self.client:
                self.device1 = await self.client.generic_device(self.device1_ip)
                self.device2 = await self.client.generic_device(self.device2_ip)
                logger.info("Connected to Tapo devices.")
            else:
                logger.warning("Tapo client not available; skipping device connect.")
        except Exception:
            logger.exception("Error connecting to devices:")
            buzz_error(frequency=BUZZER_FREQUENCY, duration=BUZZER_DURATION)

    async def _turn_device_on(self, device_num: int, reason=""):
        """Turn on a device with cooldown protection"""
        now = time.time()
        if device_num == 1:
            if (now - self.device1_last_on) > self.cooldown_seconds:
                self.device1_last_on = now
                self.device1_on = True
                state["device1"] = True
                logger.info(f" ~ DEVICE 1 ON{' (' + reason + ')' if reason else ''}")
                await self.device1.on()
                # Only schedule auto-off if not in force_on mode
                if self.device1_mode != "force_on":
                    task = asyncio.create_task(self._auto_off(1))
                    self._auto_off_tasks.add(task)
                    task.add_done_callback(lambda t: self._auto_off_tasks.discard(t))
        elif device_num == 2:
            if (now - self.device2_last_on) > self.cooldown_seconds:
                self.device2_last_on = now
                self.device2_on = True
                state["device2"] = True
                logger.info(f" ~ DEVICE 2 ON{' (' + reason + ')' if reason else ''}")
                await self.device2.on()
                # Only schedule auto-off if not in force_on mode
                if self.device2_mode != "force_on":
                    task = asyncio.create_task(self._auto_off(2))
                    self._auto_off_tasks.add(task)
                    task.add_done_callback(lambda t: self._auto_off_tasks.discard(t))

    async def _turn_device_off(self, device_num: int, reason=""):
        """Turn off a device"""
        if device_num == 1:
            if self.device1_on:
                self.device1_on = False
                state["device1"] = False
                await self.device1.off()
                logger.info(f" ~ DEVICE 1 OFF{' (' + reason + ')' if reason else ''}")
        elif device_num == 2:
            if self.device2_on:
                self.device2_on = False
                state["device2"] = False
                await self.device2.off()
                logger.info(f" ~ DEVICE 2 OFF{' (' + reason + ')' if reason else ''}")

    async def _auto_off(self, device_num: int):
        await asyncio.sleep(self.max_on_duration)
        if device_num == 1 and self.device1_on:
            await self._turn_device_off(1, "auto-off timer")
        elif device_num == 2 and self.device2_on:
            await self._turn_device_off(2, "auto-off timer")

    async def control(self, humidity, co2, temp):
        try:
            # Reload device modes from config dynamically
            self._reload_device_modes()
            
            # === DEVICE 1 (HUMIDITY) CONTROL ===
            if self.device1_mode == "force_on":
                # Force on mode - always try to turn on
                if not self.device1_on:
                    await self._turn_device_on(1, "MANUAL OVERRIDE - FORCE ON")
            elif self.device1_mode == "force_off":
                # Force off mode - always turn off
                if self.device1_on:
                    await self._turn_device_off(1, "MANUAL OVERRIDE - FORCE OFF")
            else:
                # Auto mode - use humidity threshold
                if humidity is not None:
                    if humidity < HUMIDITY_THRESHOLD:
                        await self._turn_device_on(1, f"humidity {humidity:.1f}% < {HUMIDITY_THRESHOLD}%")
                    elif humidity > HUMIDITY_THRESHOLD + HUMIDITY_HYSTERESIS:
                        await self._turn_device_off(1, f"humidity {humidity:.1f}% > {HUMIDITY_THRESHOLD + HUMIDITY_HYSTERESIS}%")
            
            # === DEVICE 2 (AIR/FAN) CONTROL ===
            if self.device2_mode == "force_on":
                # Force on mode - always try to turn on
                if not self.device2_on:
                    await self._turn_device_on(2, "MANUAL OVERRIDE - FORCE ON")
            elif self.device2_mode == "force_off":
                # Force off mode - always turn off
                if self.device2_on:
                    await self._turn_device_off(2, "MANUAL OVERRIDE - FORCE OFF")
            else:
                # Auto mode - use CO2 threshold
                if co2 is not None:
                    if co2 > CO2_THRESHOLD:
                        await self._turn_device_on(2, f"CO2 {co2:.0f}ppm > {CO2_THRESHOLD}ppm")
                    elif co2 < CO2_THRESHOLD - CO2_HYSTERESIS:
                        await self._turn_device_off(2, f"CO2 {co2:.0f}ppm < {CO2_THRESHOLD - CO2_HYSTERESIS}ppm")
                    
        except Exception:
            logger.exception("Error controlling devices")
            buzz_error(frequency=BUZZER_FREQUENCY, duration=BUZZER_DURATION)
    
    def _reload_device_modes(self):
        """Dynamically reload device modes from config file"""
        try:
            import config
            importlib.reload(config)
            
            new_device1_mode = getattr(config, 'DEVICE_1_MODE', 'auto')
            new_device2_mode = getattr(config, 'DEVICE_2_MODE', 'auto')
            
            # Log if modes changed
            if new_device1_mode != self.device1_mode:
                logger.info(f" *** Device 1 Mode Changed: {self.device1_mode} -> {new_device1_mode}")
                self.device1_mode = new_device1_mode
                state["device1_mode"] = new_device1_mode
                
            if new_device2_mode != self.device2_mode:
                logger.info(f" *** Device 2 Mode Changed: {self.device2_mode} -> {new_device2_mode}")
                self.device2_mode = new_device2_mode
                state["device2_mode"] = new_device2_mode
                
        except Exception as e:
            logger.debug(f"Config reload issue (non-critical): {e}")
            
#SENSOR LOOP (ASYNC)
async def sensor_loop(controller, i2c_sensor):
    await controller.connect()
    while not shutdown_event.is_set():
        try:
            hum, co2, temp = i2c_sensor.get_average()
            state["sensor1"] = hum
            state["sensor2"] = co2
            state["sensor3"] = temp
            await controller.control(hum, co2, temp)
            await asyncio.sleep(CHECK_INTERVAL)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Error in sensor loop")
            buzz_error(frequency=BUZZER_FREQUENCY, duration=BUZZER_DURATION)
            await asyncio.sleep(max(1, CHECK_INTERVAL))

# ============================================================================
# IMPROVED CAMERA MANAGER
# ============================================================================

class CameraManager:
    def __init__(self):
        self.cap = None
        self.lock = threading.Lock()
        self.last_access = 0
        self.access_timeout = 5  # seconds before releasing idle camera
        self.is_streaming = False
        
    def get_camera(self):
        """Get or create camera instance with proper locking"""
        with self.lock:
            if self.cap is None or not self.cap.isOpened():
                self._open_camera()
            self.last_access = time.time()
            return self.cap
    
    def _open_camera(self):
        """Internal method to open camera"""
        # Release old camera if exists
        if self.cap is not None:
            try:
                self.cap.release()
            except:
                pass
            time.sleep(0.5)  # Give camera time to release
        
        try:
            dev = CAMERA_DEVICE
            if isinstance(dev, str) and dev.isdigit():
                dev = int(dev)
            
            self.cap = cv2.VideoCapture(dev)
            
            if not self.cap.isOpened():
                logger.warning(f"Camera {CAMERA_DEVICE} not opened. Trying fallback 0.")
                self.cap = cv2.VideoCapture(0)
                
            if self.cap.isOpened():
                # Set properties with error handling
                try:
                    self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
                    self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
                    self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # Reduce buffer lag
                except Exception as e:
                    logger.debug(f"Failed to set camera properties: {e}")
                
                logger.info("Camera opened successfully")
            else:
                logger.error("Unable to open camera")
                self.cap = None
                
        except Exception as e:
            logger.exception(f"Exception opening camera: {e}")
            buzz_error(frequency=1000, duration=1.0)
            self.cap = None
    
    def release(self):
        """Safely release camera"""
        with self.lock:
            if self.cap is not None:
                try:
                    self.cap.release()
                    logger.info("Camera released")
                except:
                    pass
                self.cap = None
    
    def read_frame(self, timeout=2.0):
        """Read a frame with timeout protection"""
        start_time = time.time()
        cap = self.get_camera()
        
        if cap is None:
            return False, None
        
        # Try reading with timeout
        while time.time() - start_time < timeout:
            try:
                success, frame = cap.read()
                if success and frame is not None:
                    return True, frame
                time.sleep(0.01)
            except Exception as e:
                logger.error(f"Frame read error: {e}")
                buzz_error(frequency=1000, duration=1.0)
                break
        
        # If we failed, release and retry once
        logger.warning("Frame read timeout, reopening camera")
        self.release()
        time.sleep(0.5)
        
        cap = self.get_camera()
        if cap is not None:
            try:
                return cap.read()
            except:
                return False, None
        
        return False, None

# Create global camera manager
camera_manager = CameraManager()

def _generate_black_frame_jpeg():
    try:
        import numpy as np
        black = np.zeros((480, 640, 3), dtype="uint8")
        ret, buffer = cv2.imencode(".jpg", black)
        if not ret: return None
        return buffer.tobytes()
    except Exception:
        logger.exception("Failed to create black fallback image.")
        buzz_error(frequency=1000, duration=1.0)
        return None

FALLBACK_FRAME_BYTES = _generate_black_frame_jpeg()

def generate_frames():
    """Improved frame generator with proper resource management"""
    camera_manager.is_streaming = True
    frame_count = 0
    error_count = 0
    max_errors = 10
    
    try:
        while not shutdown_event.is_set():
            success, frame = camera_manager.read_frame(timeout=2.0)
            
            if not success or frame is None:
                error_count += 1
                logger.warning(f"Frame read failed (error {error_count}/{max_errors})")
                
                if error_count >= max_errors:
                    logger.error("Too many frame errors, releasing camera")
                    camera_manager.release()
                    error_count = 0
                    time.sleep(2.0)
                
                # Send fallback frame
                if FALLBACK_FRAME_BYTES:
                    yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" 
                           + FALLBACK_FRAME_BYTES + b"\r\n")
                time.sleep(0.1)
                continue
            
            # Reset error count on success
            error_count = 0
            frame_count += 1
            
            try:
                frame = cv2.flip(frame, 0)
            except:
                pass
            
            ret, buffer = cv2.imencode(".jpg", frame, 
                                       [cv2.IMWRITE_JPEG_QUALITY, 85])
            if not ret:
                continue
            
            frame_bytes = buffer.tobytes()
            yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" 
                   + frame_bytes + b"\r\n")
            
            # Small delay to prevent overwhelming
            time.sleep(0.033)  # ~30fps
            
    except GeneratorExit:
        logger.info("Frame generator closed by client")
    except Exception as e:
        logger.exception(f"Unexpected error in generate_frames: {e}")
        buzz_error(frequency=1000, duration=1.0)
    finally:
        camera_manager.is_streaming = False
        # Don't release camera here - let it be reused

# === FLASK ROUTES ===
@app.route("/")
def index():
    try:
        return render_template("Dashboard.html")
    except Exception:
        logger.exception("Failed to render Dashboard.html.")
        buzz_error(frequency=1000, duration=1.0)
        return "<html><body><h1>Dashboard</h1></body></html>"

@app.route("/status")
def get_status():
    snapshot = dict(state)
    return jsonify(snapshot)

@app.route("/logs")
def logs():
    with log_lock:
        response = make_response(jsonify(list(log_entries)))
        response.headers["Cache-Control"] = "no-store"
        return response 

@app.route("/video_feed")
def video_feed():
    return Response(generate_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

#DATA LOGGING AND SNAPSHOT
def capture_snapshot():
    """
    Improved snapshot capture with better conflict handling
    """
    usb_dir, _, usb_img_dir = get_usb_dirs()
    save_dir = usb_img_dir if usb_dir else LOCAL_IMG_DIR
    os.makedirs(save_dir, exist_ok=True)
    
    # Wait if streaming is active
    wait_count = 0
    while camera_manager.is_streaming and wait_count < 10:
        time.sleep(0.5)
        wait_count += 1
    
    success, frame = camera_manager.read_frame(timeout=3.0)
    
    if not success or frame is None:
        logger.warning("capture_snapshot: no frame captured")
        return None
    
    try:
        frame = cv2.flip(frame, 0)
    except Exception:
        logger.debug("capture_snapshot: flip failed")
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    img_filename = f"img_{timestamp}.jpg"
    img_path = os.path.join(save_dir, img_filename)
    tmp_path = img_path + ".tmp"
    
    try:
        ok, buffer = cv2.imencode(".jpg", frame, 
                                  [cv2.IMWRITE_JPEG_QUALITY, 95])
        if not ok:
            logger.error("capture_snapshot: encode failed")
            return None
        
        with open(tmp_path, "wb") as f:
            f.write(buffer.tobytes())
            f.flush()
            os.fsync(f.fileno())
        
        os.replace(tmp_path, img_path)
        location = "USB" if usb_dir else "local"
        logger.info(f" ~ Snapshot saved to {location}: {img_path}")
        return img_filename
        
    except Exception as e:
        logger.exception(f"Failed to write snapshot to {img_path}: {e}")
        buzz_error(frequency=1000, duration=1.0)
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except:
                pass
        return None


def _seconds_until_next_hour():
    now = datetime.now()
    next_hour = (now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
    return max(0, (next_hour - now).total_seconds())

#USB LOG TRANSFER AND PATH HELPERS
def get_usb_root():
    if os.path.ismount(USB_MOUNT_POINT):
        return USB_MOUNT_POINT
    return None

def get_usb_dirs():
    root = get_usb_root()
    if not root:
        return None, None, None
    usb_dir = os.path.join(root, USB_SUBDIR)
    usb_img_dir = os.path.join(usb_dir, USB_IMG_SUBDIR_NAME)
    usb_csv_path = os.path.join(usb_dir, USB_CSV_NAME)
    return usb_dir, usb_csv_path, usb_img_dir

def is_usb_mounted():
    return get_usb_root() is not None

def _file_has_header(file_path, expected_header):
    try:
        with open(file_path, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            first = next(reader, None)
            if first is None:
                return False
            # normalize whitespace and compare lower-case
            return [c.strip().lower() for c in first] == [c.strip().lower() for c in expected_header]
    except Exception:
        return False

def append_local_csv_to_usb(local_csv, usb_csv, header):
    try:
        if not os.path.exists(local_csv):
            logger.debug("No local CSV to append.")
            return True  # nothing to do

        os.makedirs(os.path.dirname(usb_csv) or ".", exist_ok=True)

        # checking if it exists
        usb_has = os.path.exists(usb_csv)
        local_has_header = _file_has_header(local_csv, header)

        with CSV_LOCK:
            # open usb for append or create
            with open(usb_csv, "a", newline="", encoding="utf-8") as dst_f, \
                 open(local_csv, "r", newline="", encoding="utf-8") as src_f:
                writer = csv.writer(dst_f)
                reader = csv.reader(src_f)
                # if usb doesn't exist yet and local has header - write header first to usb
                if (not usb_has) and local_has_header:
                    hdr = next(reader, None)
                    if hdr:
                        writer.writerow(hdr)
                elif (not usb_has) and (not local_has_header):
                    # usb didn't exist and local has no header => optionally write header
                    writer.writerow(header)
                else:
                    # usb exists if local has header - skip that 
                    if local_has_header:
                        next(reader, None)

                # now append rest rows
                rows_written = 0
                for row in reader:
                    if row:
                        writer.writerow(row)
                        rows_written += 1

        logger.info(f" ~ Appended {rows_written} rows from {local_csv} to {usb_csv}")
        return True
    except Exception:
        logger.exception("Failed to append local CSV to USB.")
        buzz_error(frequency=1000, duration=1.0)
        return False

def transfer_logs_to_usb():
    """Transfer local CSV and images to USB, deleting local images after successful copy."""
    try:
        if not os.path.ismount(USB_MOUNT_POINT):
            logger.info("USB not detected - storing logs locally.")
            return

        # Ensure USB directories exist
        usb_dir = os.path.join(USB_MOUNT_POINT, USB_SUBDIR)
        usb_img_dir = os.path.join(usb_dir, USB_IMG_SUBDIR_NAME)
        os.makedirs(usb_dir, exist_ok=True)
        os.makedirs(usb_img_dir, exist_ok=True)

        # === SYNC CSV ===
        usb_csv_path = os.path.join(usb_dir, USB_CSV_NAME)
        header = ["DateLogged", "Time", "Humidity", "CO2", "Temp", "img_id"]

        rows_to_append = []
        with CSV_LOCK:
            if os.path.exists(LOCAL_CSV_PATH):
                with open(LOCAL_CSV_PATH, "r", encoding="utf-8") as f:
                    reader = list(csv.reader(f))
                rows_to_append = reader[1:] if len(reader) > 1 else []

            if rows_to_append:
                usb_exists = os.path.exists(usb_csv_path)
                with open(usb_csv_path, "a", newline="", encoding="utf-8") as f_usb:
                    writer = csv.writer(f_usb)
                    if not usb_exists:
                        writer.writerow(header)
                    writer.writerows(rows_to_append)
                logger.info(f" ~ Appended {len(rows_to_append)} rows to {usb_csv_path}")

                # delete local CSV after transfer
                try:
                    os.remove(LOCAL_CSV_PATH)
                    logger.info(f" (X) Deleted local CSV {LOCAL_CSV_PATH} after transfer")
                except Exception as e:
                    logger.error(f" ~ Failed to delete local CSV: {e}")
                    buzz_error(frequency=1000, duration=1.0)

        # === IMAGE SYNC & DELETE LOCAL COPIES ===
        copied_count = 0
        if os.path.exists(IMG_DIR):
            for f in os.listdir(IMG_DIR):
                src = os.path.join(IMG_DIR, f)
                dst = os.path.join(usb_img_dir, f)
                if os.path.isfile(src):
                    try:
                        shutil.copy2(src, dst)  # copy to USB
                        os.remove(src)           # delete local copy after successful copy
                        copied_count += 1
                    except Exception as e:
                        logger.error(f" ~ Failed to copy/delete {src}: {e}")
                        buzz_error(frequency=1000, duration=1.0)
            if copied_count > 0:
                logger.info(f" ~ Copied and removed {copied_count} images from local folder to {usb_img_dir}")

    except Exception as e:
        logger.error(f"USB transfer failed: {e}")
        buzz_error(frequency=1000, duration=1.0)


def usb_transfer_loop():
    while not shutdown_event.is_set():
        if is_usb_mounted():
            try:
                transfer_logs_to_usb()
            except Exception:
                logger.exception("USB transfer failed")
                buzz_error(frequency=1000, duration=1.0)
            time.sleep(30)
        else:
            # poll more often while waiting for USB to be mounted
            time.sleep(5)

#CSV WRITE
def _write_csv_row(row, header=None):
    os.makedirs(os.path.dirname(os.path.abspath(LOCAL_CSV_PATH)) or ".", exist_ok=True)
    with CSV_LOCK:
        file_exists = os.path.isfile(LOCAL_CSV_PATH)
        try:
            with open(LOCAL_CSV_PATH, mode="a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if (not file_exists) and header:
                    writer.writerow(header)
                writer.writerow(row)
            return True
        except Exception:
            logger.exception("Failed to write row to CSV.")
            buzz_error(frequency=1000, duration=1.0)
            return False

def _log_once():
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")
    img_filename = capture_snapshot()
    row = [
        date_str,
        time_str,
        round(float(state.get("sensor1", 0.0)), 2),
        round(float(state.get("sensor2", 0.0)), 2),
        round(float(state.get("sensor3", 0.0)), 2),
        img_filename or ""
    ]
    header = ["DateLogged", "Time", "Humidity", "CO2", "Temp", "img_id"]
    if _write_csv_row(row, header=header):
        logger.info(f" ~ Logged data at {time_str} (img: {img_filename or 'none'})")
    else:
        logger.error("Failed to log data to CSV")
    try:
        transfer_logs_to_usb()
    except Exception:
        logger.exception("transfer_logs_to_usb threw during _log_once")
        buzz_error(frequency=1000, duration=1.0)

def log_data_loop():
    if LOG_ON_STARTUP:
        wait_for_initial_sensor_readings(timeout=10)  # wait up to 10 seconds
        try:
            _log_once()
        except Exception:
            logger.exception("Immediate startup log failed.")
            buzz_error(frequency=1000, duration=1.0)
    while not shutdown_event.is_set():
        try:
            sleep_seconds = _seconds_until_next_hour()
            logger.info(f"Next hourly log in {int(sleep_seconds)} seconds.")
            slept = 0.0
            while slept < sleep_seconds and not shutdown_event.is_set():
                to_sleep = min(1.0, sleep_seconds - slept)
                time.sleep(to_sleep)
                slept += to_sleep
            if shutdown_event.is_set(): break
            _log_once()
        except Exception:
            logger.exception("Exception in log_data_loop; retrying in 60s.")
            buzz_error(frequency=1000, duration=1.0)
            for _ in range(60):
                if shutdown_event.is_set(): break
                time.sleep(1)

#BACKGROUND THREADS & RUNNER
def run_async_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    controller = DeviceController(TAPO_EMAIL, TAPO_PASSWORD, DEVICE_1_IP, DEVICE_2_IP)
    i2c_sensor = I2CSensor()

    async def wrapped_sensor_loop():
        await controller.connect()
        while not shutdown_event.is_set():
            try:
                hum, co2, temp = i2c_sensor.get_average()
                state["sensor1"] = hum
                state["sensor2"] = co2
                state["sensor3"] = temp
                await controller.control(hum, co2, temp)
                await asyncio.sleep(CHECK_INTERVAL)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception(" ~ Error in I2C sensor loop:")
                buzz_error(frequency=1000, duration=1.0)
                await asyncio.sleep(max(1, CHECK_INTERVAL))

    loop.run_until_complete(wrapped_sensor_loop())

def cleanup_camera():
    """Call this during shutdown"""
    camera_manager.release()

def _signal_handler(signum, frame):
    logger.info(f"Received signal {signum}; shutting down.")
    shutdown_event.set()
    cleanup_camera()

signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)

def wait_for_initial_sensor_readings(timeout=10):
    """Wait until I2C sensors have non-zero readings or until timeout (seconds)."""
    start = time.time()
    while time.time() - start < timeout:
        hum, co2, temp = state.get("sensor1", 0.0), state.get("sensor2", 0.0), state.get("sensor3", 0.0)
        if hum > 0 and co2 > 0 and temp > 0:
            return True
        time.sleep(0.5)
    logger.warning("Timeout waiting for initial sensor readings; logging may be zeros.")
    return False

def start_background_threads():
    async_thread = threading.Thread(target=run_async_loop, name="async_thread", daemon=True)
    async_thread.start()
    log_thread = threading.Thread(target=log_data_loop, name="log_thread", daemon=True)
    log_thread.start()
    usb_thread = threading.Thread(target=usb_transfer_loop, name="usb_thread", daemon=True)
    usb_thread.start()
    return async_thread, log_thread, usb_thread

if __name__ == "__main__":
    os.makedirs(LOCAL_IMG_DIR, exist_ok=True)

    controller = DeviceController(TAPO_EMAIL, TAPO_PASSWORD, DEVICE_1_IP, DEVICE_2_IP)

    # pass controller to async thread
    def run_async_loop_with_controller():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        i2c_sensor = I2CSensor()

        async def wrapped_sensor_loop():
            await controller.connect()
            while not shutdown_event.is_set():
                try:
                    hum, co2, temp = i2c_sensor.get_average()
                    state["sensor1"] = hum
                    state["sensor2"] = co2
                    state["sensor3"] = temp
                    await controller.control(hum, co2, temp)
                    await asyncio.sleep(CHECK_INTERVAL)
                except asyncio.CancelledError:
                    break
                except Exception:
                    logger.exception(" ~ Error in I2C sensor loop:")
                    buzz_error(frequency=1000, duration=1.0)
                    await asyncio.sleep(max(1, CHECK_INTERVAL))

        loop.run_until_complete(wrapped_sensor_loop())

    async_thr = threading.Thread(target=run_async_loop_with_controller, name="async_thread", daemon=True)
    async_thr.start()
    log_thr = threading.Thread(target=log_data_loop, name="log_thread", daemon=True)
    log_thr.start()
    usb_thr = threading.Thread(target=usb_transfer_loop, name="usb_thread", daemon=True)
    usb_thr.start()
    
    # Flask thread
    def run_flask():
        try:
            app.run(host="0.0.0.0", port=5000, debug=False, threaded=True, use_reloader=False)
        except Exception:
            logger.exception("Flask server crashed.")
            buzz_error(frequency=1000, duration=1.0)

    flask_thread = threading.Thread(target=run_flask, name="flask_thread", daemon=True)
    flask_thread.start()

    try:
        while not shutdown_event.is_set():
            time.sleep(0.5)
    except KeyboardInterrupt:
        shutdown_event.set()
    finally:
        shutdown_event.set()
        cleanup_camera()

        # cancel any pending auto-off tasks
        try:
            for task in list(controller._auto_off_tasks):
                task.cancel()
        except Exception:
            logger.exception("Failed to cancel auto-off tasks")

        for t in (async_thr, log_thr, usb_thr, flask_thread):
            if t.is_alive(): t.join(timeout=5)
        
        # Cleanup GPIO
        try:
            buzzer_pwm.stop()
            GPIO.cleanup()
        except Exception:
            pass
        
        logger.info("Application exited cleanly :-)")
        sys.exit(0)