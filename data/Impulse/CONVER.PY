import argparse
import math
import wave
from pathlib import Path

import numpy as np
import pandas as pd


def load_curve(csv_path, column):
    df = pd.read_csv(csv_path, skipinitialspace=True)
    df.columns = [c.strip().lower() for c in df.columns]
    values = df[column.lower()].dropna().astype(float).to_numpy()
    return values


def make_cycle(values, n_samples):
    x = np.linspace(0, 1, len(values))
    cycle = np.interp(np.linspace(0, 1, n_samples), x, values)
    cycle -= cycle.mean()
    cycle /= np.abs(cycle).max()
    return cycle


def make_tone(values, pitch, total, sr):
    spc = max(1, int(round(sr / pitch)))
    cycle = make_cycle(values, spc)
    return np.tile(cycle, math.ceil(total / spc))[:total]


def decay_env(total, decay_samples, attack_samples=0):
    env = np.exp(-np.arange(total) / decay_samples)
    if attack_samples > 1:
        env[:attack_samples] *= np.linspace(0, 1, attack_samples)
    return env


def generate(csv_path, output_path, column, duration, sr):
    values = load_curve(csv_path, column)
    total = int(sr * duration)
    atk = int(0.001 * sr)

    thud  = make_tone(values, 60,   total, sr)
    crack = make_tone(values, 800,  total, sr)
    click = make_tone(values, 6000, total, sr)
    noise = np.random.default_rng(42).standard_normal(total)
    noise /= np.abs(noise).max()

    t = np.linspace(0, duration, total)
    pitch_drop = np.sin(2 * np.pi * (60 * np.exp(-t * 8)) * t)
    thud = 0.6 * thud + 0.4 * pitch_drop

    mix = (
        0.55 * thud  * decay_env(total, int(0.25 * sr), atk) +
        0.30 * crack * decay_env(total, int(0.08 * sr), atk) +
        0.10 * click * decay_env(total, int(0.03 * sr), atk) +
        0.05 * noise * decay_env(total, int(0.02 * sr), atk)
    )

    mix /= np.abs(mix).max()
    pcm = (mix * 32767).clip(-32768, 32767).astype(np.int16)

    with wave.open(output_path, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())

    print(f"saved: {output_path}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("input")
    p.add_argument("output", nargs="?", default=None)
    p.add_argument("--column", default="raw", choices=["raw", "averaged"])
    p.add_argument("--duration", type=float, default=0.8)
    p.add_argument("--sample-rate", type=int, default=44100)
    args = p.parse_args()

    out = args.output or f"{Path(args.input).stem}.wav"
    generate(args.input, out, args.column, args.duration, args.sample_rate)


if __name__ == "__main__":
    main()