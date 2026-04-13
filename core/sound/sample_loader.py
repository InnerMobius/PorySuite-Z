"""
Loader for GBA audio samples.

Handles:
- DirectSound .bin samples (GBA WaveData header + signed 8-bit PCM)
- Programmable wave .pcm samples (16-byte waveform cycles)
- direct_sound_data.inc parsing (label -> file path mapping)
- programmable_wave_data.inc parsing
"""

from __future__ import annotations

import os
import re
import struct
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class WaveDataHeader:
    """GBA WaveData struct header (16 bytes at the start of a .bin file).

    struct WaveData {
        u16 type;       // 0 = normal, 1 = compressed, etc.
        u16 status;     // flags (0x4000 = looping enabled)
        u32 freq;       // sample rate as fixed-point: actual_hz = freq >> 10
        u32 loopStart;  // byte offset into PCM data where loop begins (0 = no loop)
        u32 size;       // number of PCM data bytes
    };
    """
    type: int = 0
    status: int = 0
    freq_raw: int = 0       # raw frequency value (fixed-point)
    loop_start: int = 0     # byte offset for sustain loop (0 = no loop)
    size: int = 0           # PCM data byte count

    @property
    def sample_rate(self) -> int:
        """Actual sample rate in Hz (freq >> 10)."""
        return self.freq_raw >> 10

    @property
    def is_looping(self) -> bool:
        """Whether the sample has looping enabled (status flag 0x4000)."""
        return bool(self.status & 0x4000)


@dataclass
class DirectSoundSample:
    """A loaded DirectSound sample."""
    label: str               # e.g. 'DirectSoundWaveData_sc88pro_organ2'
    file_path: str           # full path to the .bin file
    header: WaveDataHeader = field(default_factory=WaveDataHeader)
    pcm_data: bytes = b''   # signed 8-bit PCM audio

    @property
    def friendly_name(self) -> str:
        name = self.label
        if name.startswith('DirectSoundWaveData_'):
            name = name[len('DirectSoundWaveData_'):]
        return name.replace('_', ' ').title()

    @property
    def sample_rate(self) -> int:
        return self.header.sample_rate

    @property
    def has_loop(self) -> bool:
        return self.header.is_looping and self.header.loop_start > 0

    @property
    def duration_seconds(self) -> float:
        rate = self.header.sample_rate
        if rate > 0:
            return len(self.pcm_data) / rate
        return 0.0

    @property
    def pcm_float(self) -> list[float]:
        """Convert signed 8-bit PCM to float32 [-1.0, 1.0]."""
        return [((b if b < 128 else b - 256) / 128.0) for b in self.pcm_data]


@dataclass
class ProgrammableWaveSample:
    """A loaded programmable wave sample (16 bytes, 4-bit packed)."""
    label: str               # e.g. 'ProgrammableWaveData_1'
    file_path: str
    raw_data: bytes = b''   # 16 bytes

    @property
    def friendly_name(self) -> str:
        num = self.label.split('_')[-1] if '_' in self.label else '?'
        return f'Wave {num}'

    @property
    def samples_4bit(self) -> list[int]:
        """Unpack 16 bytes into 32 4-bit samples (0-15)."""
        result = []
        for byte in self.raw_data:
            result.append((byte >> 4) & 0x0F)  # high nibble first
            result.append(byte & 0x0F)          # low nibble
        return result


@dataclass
class SampleData:
    """All loaded sample data from a project."""
    direct_sound: dict[str, DirectSoundSample] = field(default_factory=dict)
    programmable_waves: dict[str, ProgrammableWaveSample] = field(default_factory=dict)

    # Label -> relative file path mapping (from .inc files)
    _ds_label_to_path: dict[str, str] = field(default_factory=dict, repr=False)
    _pw_label_to_path: dict[str, str] = field(default_factory=dict, repr=False)


# ---------------------------------------------------------------------------
# .bin file reader (GBA WaveData format)
# ---------------------------------------------------------------------------

WAVEDATA_HEADER_SIZE = 16  # bytes


def read_bin_sample(filepath: str) -> tuple[WaveDataHeader, bytes]:
    """Read a .bin DirectSound sample file.

    Returns (header, pcm_data).
    """
    with open(filepath, 'rb') as f:
        raw = f.read()

    if len(raw) < WAVEDATA_HEADER_SIZE:
        return WaveDataHeader(), b''

    # Parse the 16-byte header: u16 type, u16 status, u32 freq, u32 loopStart, u32 size
    type_, status, freq_raw, loop_start, size = struct.unpack_from('<HHIII', raw, 0)

    header = WaveDataHeader(
        type=type_,
        status=status,
        freq_raw=freq_raw,
        loop_start=loop_start,
        size=size,
    )

    # PCM data follows the header
    pcm_data = raw[WAVEDATA_HEADER_SIZE:WAVEDATA_HEADER_SIZE + size]

    return header, pcm_data


# ---------------------------------------------------------------------------
# .inc file parsers (label -> file path mapping)
# ---------------------------------------------------------------------------

_RE_DS_LABEL = re.compile(r'^(\w+)::')
_RE_INCBIN = re.compile(r'\.incbin\s+"([^"]+)"')


def _parse_direct_sound_data_inc(filepath: str) -> dict[str, str]:
    """Parse direct_sound_data.inc -> {label: relative_bin_path}."""
    mapping: dict[str, str] = {}
    current_label = None

    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            stripped = line.strip()
            m = _RE_DS_LABEL.match(stripped)
            if m:
                current_label = m.group(1)
                continue
            m = _RE_INCBIN.search(stripped)
            if m and current_label:
                mapping[current_label] = m.group(1)
                current_label = None

    return mapping


def _parse_programmable_wave_data_inc(filepath: str) -> dict[str, str]:
    """Parse programmable_wave_data.inc -> {label: relative_pcm_path}."""
    # Same format as direct_sound_data.inc
    return _parse_direct_sound_data_inc(filepath)


# ---------------------------------------------------------------------------
# Main loader
# ---------------------------------------------------------------------------

def load_sample_data(
    project_root: str,
    load_pcm: bool = True,
) -> SampleData:
    """Load all sample data from a project.

    Args:
        project_root: Path to the pokefirered project root.
        load_pcm: If True, read and decode the actual audio data.
                  If False, only load metadata (faster for browsing).
    """
    ds_inc_path = os.path.join(project_root, 'sound', 'direct_sound_data.inc')
    pw_inc_path = os.path.join(project_root, 'sound', 'programmable_wave_data.inc')

    data = SampleData()

    # --- DirectSound samples ---
    if os.path.isfile(ds_inc_path):
        data._ds_label_to_path = _parse_direct_sound_data_inc(ds_inc_path)

        for label, rel_path in data._ds_label_to_path.items():
            # Skip cry samples (they're in the cries/ subdirectory)
            if '/cries/' in rel_path or '\\cries\\' in rel_path:
                continue

            abs_path = os.path.join(project_root, rel_path.replace('/', os.sep))

            sample = DirectSoundSample(
                label=label,
                file_path=abs_path,
            )

            if load_pcm and os.path.isfile(abs_path):
                try:
                    header, pcm = read_bin_sample(abs_path)
                    sample.header = header
                    sample.pcm_data = pcm
                except Exception as e:
                    print(f"Warning: failed to read sample {abs_path}: {e}")

            data.direct_sound[label] = sample

    # --- Programmable wave samples ---
    if os.path.isfile(pw_inc_path):
        data._pw_label_to_path = _parse_programmable_wave_data_inc(pw_inc_path)

        for label, rel_path in data._pw_label_to_path.items():
            abs_path = os.path.join(project_root, rel_path.replace('/', os.sep))

            pw_sample = ProgrammableWaveSample(
                label=label,
                file_path=abs_path,
            )

            if load_pcm and os.path.isfile(abs_path):
                try:
                    with open(abs_path, 'rb') as f:
                        pw_sample.raw_data = f.read(16)
                except Exception as e:
                    print(f"Warning: failed to read wave {abs_path}: {e}")

            data.programmable_waves[label] = pw_sample

    return data


def get_sample_for_instrument(
    sample_data: SampleData,
    sample_label: Optional[str],
) -> Optional[DirectSoundSample]:
    """Look up a DirectSound sample by its label."""
    if sample_label:
        return sample_data.direct_sound.get(sample_label)
    return None


def get_wave_for_instrument(
    sample_data: SampleData,
    wave_label: Optional[str],
) -> Optional[ProgrammableWaveSample]:
    """Look up a programmable wave sample by its label."""
    if wave_label:
        return sample_data.programmable_waves.get(wave_label)
    return None


# ---------------------------------------------------------------------------
# Sample management — export, import, replace, delete
# ---------------------------------------------------------------------------

import wave as _wave_mod
import shutil
import array as _array_mod


def export_sample_to_wav(
    sample: DirectSoundSample,
    output_path: str,
) -> None:
    """Export a DirectSound sample to a standard WAV file.

    Converts signed 8-bit PCM to unsigned 8-bit (WAV standard for 8-bit).
    """
    if not sample.pcm_data:
        raise ValueError(f"Sample '{sample.label}' has no PCM data loaded")

    rate = sample.header.sample_rate
    if rate <= 0:
        rate = 13379  # fallback to common GBA rate

    # WAV 8-bit is unsigned (0-255), GBA 8-bit is signed (-128 to 127)
    unsigned_pcm = bytes((b + 128) & 0xFF for b in sample.pcm_data)

    with _wave_mod.open(output_path, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(1)  # 8-bit = 1 byte
        wf.setframerate(rate)
        wf.writeframes(unsigned_pcm)


def peek_wav_info(wav_path: str) -> dict:
    """Read basic info from a WAV file without converting it.

    Returns dict with: rate, channels, sampwidth, nframes, duration,
    mono_8bit_size (estimated bytes as 8-bit mono).
    """
    with _wave_mod.open(wav_path, 'rb') as wf:
        rate = wf.getframerate()
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        nframes = wf.getnframes()

    # After conversion to mono 8-bit, size = nframes (one byte per sample)
    duration = nframes / rate if rate > 0 else 0
    return {
        'rate': rate,
        'channels': channels,
        'sampwidth': sampwidth,
        'nframes': nframes,
        'duration': duration,
        'mono_8bit_size': nframes,  # 1 byte per mono sample
    }


def _read_wav_as_signed8(wav_path: str) -> tuple[int, bytes]:
    """Read any WAV file and convert to mono signed 8-bit PCM.

    Handles 8-bit (unsigned), 16-bit, 24-bit, and 32-bit WAV files.
    Stereo is mixed to mono. Returns (sample_rate, signed_8bit_bytes).
    """
    with _wave_mod.open(wav_path, 'rb') as wf:
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        rate = wf.getframerate()
        nframes = wf.getnframes()
        raw = wf.readframes(nframes)

    if sampwidth not in (1, 2, 3, 4):
        raise ValueError(
            f"Unsupported bit depth: {sampwidth * 8}-bit.\n"
            "WAV must be 8-bit, 16-bit, 24-bit, or 32-bit PCM.")

    # Decode to list of float samples [-1.0, 1.0], mono
    samples: list[float] = []

    if sampwidth == 1:
        # 8-bit WAV is unsigned (0–255), center is 128
        for i in range(0, len(raw), channels):
            if channels > 1:
                total = sum(raw[i + c] for c in range(channels))
                val = (total / channels - 128) / 128.0
            else:
                val = (raw[i] - 128) / 128.0
            samples.append(val)

    elif sampwidth == 2:
        # 16-bit WAV is signed little-endian
        arr = _array_mod.array('h')
        arr.frombytes(raw)
        for i in range(0, len(arr), channels):
            if channels > 1:
                total = sum(arr[i + c] for c in range(channels))
                val = (total / channels) / 32768.0
            else:
                val = arr[i] / 32768.0
            samples.append(val)

    elif sampwidth == 3:
        # 24-bit WAV — 3 bytes per sample, signed
        for i in range(0, len(raw), 3 * channels):
            ch_sum = 0.0
            for c in range(channels):
                off = i + c * 3
                b0, b1, b2 = raw[off], raw[off + 1], raw[off + 2]
                val_24 = b0 | (b1 << 8) | (b2 << 16)
                if val_24 & 0x800000:
                    val_24 -= 0x1000000
                ch_sum += val_24 / 8388608.0
            samples.append(ch_sum / channels if channels > 1 else ch_sum)

    elif sampwidth == 4:
        # 32-bit WAV is signed little-endian
        arr = _array_mod.array('i')
        arr.frombytes(raw)
        for i in range(0, len(arr), channels):
            if channels > 1:
                total = sum(arr[i + c] for c in range(channels))
                val = (total / channels) / 2147483648.0
            else:
                val = arr[i] / 2147483648.0
            samples.append(val)

    # Convert float samples to signed 8-bit (-128 to 127)
    signed_bytes = bytearray(len(samples))
    for i, s in enumerate(samples):
        clamped = max(-1.0, min(1.0, s))
        signed_bytes[i] = int(clamped * 127) & 0xFF
    return rate, bytes(signed_bytes)


def _write_gba_bin(
    output_path: str,
    sample_rate: int,
    pcm_data: bytes,
    loop: bool = False,
    loop_start: int = 0,
) -> None:
    """Write a GBA WaveData .bin file (16-byte header + signed 8-bit PCM)."""
    type_ = 0  # normal (uncompressed)
    status = 0x4000 if loop else 0
    freq_raw = sample_rate << 10  # fixed-point frequency
    size = len(pcm_data)

    header = struct.pack('<HHIII', type_, status, freq_raw, loop_start, size)

    with open(output_path, 'wb') as f:
        f.write(header)
        f.write(pcm_data)


def import_wav_as_sample(
    project_root: str,
    wav_path: str,
    label_suffix: str,
    sample_data: SampleData,
    target_rate: int = 0,
) -> DirectSoundSample:
    """Import a WAV file as a new DirectSound sample.

    Converts the WAV to GBA .bin format (signed 8-bit PCM with WaveData
    header), adds it to direct_sound_data.inc, and returns the new sample.

    Args:
        project_root: pokefirered project root path
        wav_path: Path to the source WAV file
        label_suffix: Name part after 'DirectSoundWaveData_' (e.g. 'my_trumpet')
        sample_data: Current SampleData to update in-place
        target_rate: If > 0, resample to this rate (saves ROM space)

    Returns:
        The newly created DirectSoundSample
    """
    label = f"DirectSoundWaveData_{label_suffix}"
    if label in sample_data.direct_sound:
        raise ValueError(f"A sample with label '{label}' already exists")

    # Determine output .bin path
    bin_name = f"{label_suffix}.bin"
    bin_rel = f"sound/direct_sound_samples/{bin_name}"
    bin_abs = os.path.join(project_root, 'sound', 'direct_sound_samples', bin_name)

    if os.path.exists(bin_abs):
        raise ValueError(f"File already exists: {bin_abs}")

    # Read and convert the WAV to signed 8-bit mono
    rate, pcm = _read_wav_as_signed8(wav_path)

    if len(pcm) == 0:
        raise ValueError("WAV file contains no audio data")

    # Resample if requested (to save ROM space)
    if target_rate > 0 and target_rate != rate:
        pcm = _resample_linear(pcm, rate, target_rate)
        rate = target_rate

    # Write the GBA .bin file
    _write_gba_bin(bin_abs, rate, pcm)

    # Append to direct_sound_data.inc
    inc_path = os.path.join(project_root, 'sound', 'direct_sound_data.inc')
    entry = f"\n\t.align 2\n{label}::\n\t.incbin \"{bin_rel}\"\n"
    with open(inc_path, 'a', encoding='utf-8') as f:
        f.write(entry)

    # Load the new sample back
    header, pcm_loaded = read_bin_sample(bin_abs)
    new_sample = DirectSoundSample(
        label=label,
        file_path=bin_abs,
        header=header,
        pcm_data=pcm_loaded,
    )

    # Update in-memory data
    sample_data.direct_sound[label] = new_sample
    sample_data._ds_label_to_path[label] = bin_rel

    return new_sample


def _resample_linear(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Resample signed 8-bit PCM data from src_rate to dst_rate.

    Uses linear interpolation.  Returns new bytes at the target rate.
    """
    if src_rate == dst_rate or len(pcm) == 0:
        return pcm

    src_len = len(pcm)
    ratio = src_rate / dst_rate
    dst_len = int(src_len / ratio)
    if dst_len == 0:
        return pcm

    # Convert to signed ints for interpolation
    src = _array_mod.array('b')
    src.frombytes(pcm)

    out = bytearray(dst_len)
    for i in range(dst_len):
        pos = i * ratio
        idx = int(pos)
        frac = pos - idx
        if idx + 1 < src_len:
            val = src[idx] * (1.0 - frac) + src[idx + 1] * frac
        else:
            val = src[min(idx, src_len - 1)]
        out[i] = int(max(-128, min(127, round(val)))) & 0xFF
    return bytes(out)


def replace_sample_from_wav(
    wav_path: str,
    existing_sample: DirectSoundSample,
    target_rate: int = 0,
) -> DirectSoundSample:
    """Replace an existing sample's audio data from a new WAV file.

    Keeps the same label and .bin file path. Overwrites the .bin file
    with the converted audio.  Preserves the original loop flag
    (loop point is scaled proportionally).

    Args:
        target_rate: If > 0, resample to this rate instead of matching
            the original.  If 0, matches the original sample's rate.
    """
    bin_abs = existing_sample.file_path

    # Capture original header values BEFORE overwriting
    orig_rate = existing_sample.header.sample_rate  # Hz (freq_raw >> 10)
    orig_loop = (existing_sample.header.status & 0x4000) != 0
    orig_loop_start = existing_sample.header.loop_start
    orig_size = existing_sample.header.size

    # Use target rate or match original
    final_rate = target_rate if target_rate > 0 else orig_rate

    # Back up the original
    backup_path = bin_abs + '.bak'
    if os.path.isfile(bin_abs):
        shutil.copy2(bin_abs, backup_path)

    try:
        # Read and convert the WAV
        wav_rate, pcm = _read_wav_as_signed8(wav_path)

        if len(pcm) == 0:
            raise ValueError("WAV file contains no audio data")

        # Resample to the target rate
        if wav_rate != final_rate and final_rate > 0:
            pcm = _resample_linear(pcm, wav_rate, final_rate)

        # Scale loop point proportionally if the original looped
        loop_start = 0
        if orig_loop and orig_size > 0:
            # Place loop point at the same relative position in the new data
            loop_frac = orig_loop_start / orig_size
            loop_start = int(loop_frac * len(pcm))

        # Write the GBA .bin with chosen rate and loop settings
        _write_gba_bin(bin_abs, final_rate, pcm,
                       loop=orig_loop, loop_start=loop_start)

        # Reload the sample data
        header, pcm_loaded = read_bin_sample(bin_abs)
        existing_sample.header = header
        existing_sample.pcm_data = pcm_loaded

    except Exception:
        # Restore backup on failure
        if os.path.isfile(backup_path):
            shutil.move(backup_path, bin_abs)
        raise

    finally:
        # Clean up backup on success
        if os.path.isfile(backup_path):
            os.remove(backup_path)

    return existing_sample


def delete_sample(
    project_root: str,
    sample: DirectSoundSample,
    sample_data: SampleData,
    voicegroup_data=None,
) -> list[str]:
    """Delete a sample and remove it from direct_sound_data.inc.

    Args:
        project_root: Project root path
        sample: The sample to delete
        sample_data: SampleData to update in-place
        voicegroup_data: Optional VoicegroupData to check for references

    Returns:
        List of voicegroup names that still reference this sample
        (empty if the sample was unused)
    """
    # Check for references
    referencing_vgs: list[str] = []
    if voicegroup_data:
        for vg in voicegroup_data.voicegroups.values():
            if sample.label in vg.used_sample_labels:
                referencing_vgs.append(vg.name)

    if referencing_vgs:
        return referencing_vgs  # caller should warn user

    label = sample.label
    bin_abs = sample.file_path

    # Remove from .inc file
    inc_path = os.path.join(project_root, 'sound', 'direct_sound_data.inc')
    if os.path.isfile(inc_path):
        _remove_inc_entry(inc_path, label)

    # Remove the .bin file
    if os.path.isfile(bin_abs):
        os.remove(bin_abs)

    # Update in-memory data
    sample_data.direct_sound.pop(label, None)
    sample_data._ds_label_to_path.pop(label, None)

    return []


def get_sample_references(
    sample_label: str,
    voicegroup_data,
) -> list[tuple[str, int]]:
    """Find all voicegroup slots that reference a sample.

    Returns list of (voicegroup_name, slot_index) tuples.
    """
    refs = []
    if not voicegroup_data:
        return refs
    for vg in voicegroup_data.voicegroups.values():
        for inst in vg.instruments:
            if inst.sample_label == sample_label:
                refs.append((vg.name, inst.slot_index))
    return refs


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _remove_inc_entry(inc_path: str, label: str) -> None:
    """Remove a label + .incbin block from an .inc file."""
    with open(inc_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # Find the label line and remove the block:
    # .align 2
    # LabelName::
    #     .incbin "path"
    # (optional blank line)
    new_lines = []
    i = 0
    while i < len(lines):
        # Check if this line is the .align before our label
        if (i + 1 < len(lines)
                and lines[i].strip() == '.align 2'
                and lines[i + 1].strip() == f'{label}::'):
            # Skip .align, label, .incbin, and trailing blank
            i += 2  # skip .align and label
            if i < len(lines) and '.incbin' in lines[i]:
                i += 1  # skip .incbin
            # Skip trailing blank line
            if i < len(lines) and lines[i].strip() == '':
                i += 1
            continue
        new_lines.append(lines[i])
        i += 1

    with open(inc_path, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)
