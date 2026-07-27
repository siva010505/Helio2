import numpy as np
import scipy.io.wavfile as wav
import os

def generate_swoosh(output_path, duration=0.8, sample_rate=44100):
    t = np.linspace(0, duration, int(sample_rate * duration), False)
    
    # Generate white noise
    noise = np.random.uniform(-1.0, 1.0, len(t))
    
    # Apply a volume envelope (fade in quickly, fade out smoothly)
    # Peak at 30% of the duration
    peak_time = 0.3 * duration
    envelope = np.zeros_like(t)
    
    for i, time in enumerate(t):
        if time < peak_time:
            envelope[i] = (time / peak_time) ** 2
        else:
            envelope[i] = ((duration - time) / (duration - peak_time)) ** 3
            
    # Apply a low-pass filter effect by smoothing the noise
    swoosh = noise * envelope
    
    # Optional: A simple sine wave frequency sweep (a "sub drop" or "whoosh" tone)
    freqs = np.linspace(400, 50, len(t))
    phase = np.cumsum(freqs / sample_rate * 2 * np.pi)
    tone = np.sin(phase) * envelope * 0.5
    
    final_audio = swoosh + tone
    
    # Normalize
    final_audio = final_audio / np.max(np.abs(final_audio))
    
    # Convert to 16-bit PCM
    audio_data = np.int16(final_audio * 32767)
    
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    wav.write(output_path, sample_rate, audio_data)
    print(f"Successfully generated swoosh at {output_path}")

if __name__ == "__main__":
    generate_swoosh("assets/music/swoosh.wav")
