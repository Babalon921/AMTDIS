<p align="center">
  <img src="https://github.com/user-attachments/assets/ee394171-0592-4676-910b-d0bb6031c6bb" alt="Plymouth Image"/>
</p>

# From Mushrooms to Music: The Creative Constraints of Mycelium Sonification
## Disclaimer: The use of built in VS Studio AI tools such as GitHub Copilot and IntelliCode have been used!
Infomation regarding AI usage within this project:
https://learn.microsoft.com/en-us/visualstudio/ide/ai-assisted-development-visual-studio?view=visualstudio

Dissertation:
[HGRAY_DISSERTATION_APRIL26.pdf](https://github.com/user-attachments/files/28651611/HGRAY_DISSERTATION_APRIL26.pdf)


Experiment Timelapse:

https://github.com/user-attachments/assets/b8068dbb-6146-424e-83da-5a7c0140560b

Example of Triggering:

[TRIGGERING EXAMPLE.wav](https://github.com/user-attachments/files/26721184/EXAMPLE.wav)

Example of Texture & Convolution Reverb:

[TEXTURE_EXAMPLE.wav](https://github.com/user-attachments/files/26721879/TEXTURE_EXAMPLE.wav)


For Cultivation Script:
```bash
Mushroom_V2.py
```
For Impulse Script:
```bash
data/Impulse/CONVER.py
```

config.py format:

```bash
# config.py
# === TAPO DEVICES ===
TAPO_EMAIL = "email"
TAPO_PASSWORD = "secretpassword*"
DEVICE_1_IP = "192.168.0.10"
DEVICE_2_IP = "192.168.0.11"

# === MANUAL OVERRIDE CONTROLS ===
# Set to "auto", "force_on", or "force_off" for each device
DEVICE_1_MODE = "auto"          # Options: "auto", "force_on", "force_off"
DEVICE_2_MODE = "force_on"          # Options: "auto", "force_on", "force_off"

# === CAMERA / SENSORS ===
CAMERA_DEVICE = "/dev/video0"
CHECK_INTERVAL = 1              # seconds between sensor reads
MOVING_AVERAGE_WINDOW = 100     # seconds for moving average

# === THRESHOLDS / CONTROL ===
HUMIDITY_THRESHOLD = 80         # turn device1 on if below this
CO2_THRESHOLD = 1500            # turn device2 on if above this
HUMIDITY_HYSTERESIS = 5         # prevent toggle near threshold
CO2_HYSTERESIS = 50

# === DEVICE SAFETY ===
DEVICE_COOLDOWN_SECONDS = 60
DEVICE_MAX_ON_DURATION = 30     # seconds, fail-safe (ignored when in force_on mode)

# === BUZZER ===
BUZZER_PIN = 5
BUZZER_FREQUENCY = 1000
BUZZER_DURATION = 0.2
BUZZER_REPEATS = 10
BUZZER_PAUSE = 0.1

# === LOGGING ===
LOG_ON_STARTUP = True
LOCAL_CSV_PATH = "data_log_local.csv"
LOCAL_IMG_DIR = "img_log"

# === USB LOGGING ===
USB_MOUNT_POINT = "/media/haz/8C11-1A81"
USB_SUBDIR = "MushroomLogs"
USB_CSV_NAME = "data_log.csv"
USB_IMG_SUBDIR_NAME = "img_log"
LOCAL_CSV = "data_log_local.csv"
```

# Arduino Script For Signal Acquisition:
```bash
void setup() {
  Serial.begin(9600);

  // Use default 5V reference
  analogReference(DEFAULT);

  // Header for clarity
  Serial.println("raw,averaged");
}

void loop() {
  const int samples = 16;
  long sum = 0;

  // Take one raw reading first
  int rawValue = analogRead(A0);

  // Then do averaging
  for (int i = 0; i < samples; i++) {
    sum += analogRead(A0);
  }

  int averagedValue = sum / samples;

  // Print raw and averaged values
  Serial.print(rawValue);
  Serial.print(",");
  Serial.println(averagedValue);

  delay(5);
}
```

## Author
- [@Babalon921](https://github.com/Babalon921/)
- Harry Gray 3rd Year Module: AMT6001
- [University of Plymouth BSc Audio Music Technology](https://www.plymouth.ac.uk/)
