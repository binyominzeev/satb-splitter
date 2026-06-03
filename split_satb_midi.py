#!/usr/bin/env python3
"""
SATB + optional solo voice aware MIDI splitter.

Usage:
    python split_satb_midi.py input.mid output.mid
"""

import argparse
from dataclasses import dataclass, field
from itertools import permutations
from typing import Optional

import pretty_midi


# ---------------------------------------------------------------------------
# SATB pitch-range priors (MIDI note numbers)
# ---------------------------------------------------------------------------
VOICE_RANGES = {
    "Soprano": (60, 81),   # C4–A5
    "Alto":    (55, 74),   # G3–D5
    "Tenor":   (48, 67),   # C3–G4
    "Bass":    (40, 60),   # E2–C4
}
MAX_VOICES = 5
RANGE_PENALTY_WEIGHT = 2.0
OVERLAP_PENALTY = 1000.0
PITCH_WEIGHT = 1.0
TIME_GAP_WEIGHT = 0.5
START_EPSILON = 1e-6


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass
class Note:
    pitch: int
    start: float
    end: float
    velocity: int


@dataclass
class Voice:
    name: str
    notes: list = field(default_factory=list)

    @property
    def last_note(self) -> Optional[Note]:
        return self.notes[-1] if self.notes else None

    @property
    def average_pitch(self) -> float:
        if not self.notes:
            return 0.0
        return sum(n.pitch for n in self.notes) / len(self.notes)


# ---------------------------------------------------------------------------
# Step 1: Load MIDI
# ---------------------------------------------------------------------------
def load_midi(path: str) -> pretty_midi.PrettyMIDI:
    """Load a MIDI file and return a PrettyMIDI object."""
    return pretty_midi.PrettyMIDI(path)


# ---------------------------------------------------------------------------
# Step 2: Extract notes from an instrument track
# ---------------------------------------------------------------------------
def extract_notes(instrument: pretty_midi.Instrument) -> list[Note]:
    """Return a list of Note objects sorted by start time then pitch descending."""
    notes = [
        Note(
            pitch=n.pitch,
            start=n.start,
            end=n.end,
            velocity=n.velocity,
        )
        for n in instrument.notes
    ]
    notes.sort(key=lambda n: (n.start, -n.pitch))
    return notes


# ---------------------------------------------------------------------------
# Step 3: Voice cost and assignment
# ---------------------------------------------------------------------------
def compute_voice_cost(note: Note, voice: Voice) -> float:
    """
    Compute assignment cost for placing *note* into *voice*.

    Lower is better.  Returns OVERLAP_PENALTY when the note would overlap
    with the voice's current last note.
    """
    last = voice.last_note

    # Hard penalty for temporal overlap
    if last is not None and note.start < last.end:
        return OVERLAP_PENALTY

    # Pitch distance from last note in this voice
    pitch_cost = abs(note.pitch - last.pitch) * PITCH_WEIGHT if last else 0.0

    # Time-gap cost: prefer voices that ended recently
    time_cost = (note.start - last.end) * TIME_GAP_WEIGHT if last else 0.0

    # Range compatibility penalty
    range_cost = _range_penalty(note.pitch, voice.name)

    return pitch_cost + time_cost + range_cost


def _range_penalty(pitch: int, voice_name: str) -> float:
    """Return a soft penalty when *pitch* falls outside the expected SATB range."""
    if voice_name not in VOICE_RANGES:
        return 0.0
    lo, hi = VOICE_RANGES[voice_name]
    if pitch < lo:
        return (lo - pitch) * RANGE_PENALTY_WEIGHT
    if pitch > hi:
        return (pitch - hi) * RANGE_PENALTY_WEIGHT
    return 0.0


def assign_voices(notes: list[Note], max_voices: int = MAX_VOICES) -> list[Voice]:
    """
    Greedily assign every note to the best-fitting voice slot.

    Voices are created on demand up to MAX_VOICES.  Initial voice names are
    temporary placeholders; they are replaced by normalize_satb() later.
    """
    voices: list[Voice] = []

    for note in notes:
        if not voices:
            voices.append(Voice(name=f"Voice_{len(voices) + 1}"))

        best_idx = None
        best_cost = float("inf")

        for idx, voice in enumerate(voices):
            cost = compute_voice_cost(note, voice)
            if cost < best_cost:
                best_cost = cost
                best_idx = idx

        # If every existing voice is blocked (overlap) and we can open a new one
        if best_cost >= OVERLAP_PENALTY and len(voices) < max_voices:
            voices.append(Voice(name=f"Voice_{len(voices) + 1}"))
            best_idx = len(voices) - 1

        voices[best_idx].notes.append(note)

    return voices


# ---------------------------------------------------------------------------
# Step 4: SATB normalization
# ---------------------------------------------------------------------------
def normalize_satb(voices: list[Voice]) -> list[Voice]:
    """
    Re-label voices according to average pitch (high → low → SATB order).

    If a 5th voice exists it is labelled "Solo".
    If fewer than 4 voices exist only the appropriate subset is labelled.
    Voices with no notes are discarded.
    """
    active = [v for v in voices if v.notes]
    active.sort(key=lambda v: v.average_pitch, reverse=True)

    satb_labels = ["Soprano", "Alto", "Tenor", "Bass", "Solo"]
    for i, voice in enumerate(active):
        if i < len(satb_labels):
            voice.name = satb_labels[i]
        else:
            voice.name = f"Unclassified_{i + 1}"

    return active


def estimate_max_polyphony(notes: list[Note]) -> int:
    """Return the maximum number of simultaneously sounding notes."""
    events: list[tuple[float, int]] = []
    for note in notes:
        events.append((note.start, 1))
        events.append((note.end, -1))

    active = 0
    peak = 0
    for _, delta in sorted(events, key=lambda event: (event[0], event[1])):
        active += delta
        peak = max(peak, active)
    return peak


def group_notes_by_start(notes: list[Note]) -> list[list[Note]]:
    """Group notes that begin at the same time."""
    groups: list[list[Note]] = []
    for note in notes:
        if not groups or abs(groups[-1][0].start - note.start) > START_EPSILON:
            groups.append([note])
        else:
            groups[-1].append(note)
    return groups


def split_two_voice_notes(notes: list[Note], source_name: str) -> list[Voice]:
    """Split a two-voice source track into stable upper/lower monophonic lines."""
    upper = Voice(name=f"{source_name} Upper")
    lower = Voice(name=f"{source_name} Lower")
    voices = [upper, lower]

    for group in group_notes_by_start(notes):
        ordered = sorted(group, key=lambda note: note.pitch, reverse=True)

        if len(ordered) == 1:
            note = ordered[0]
            best_voice = min(voices, key=lambda voice: compute_voice_cost(note, voice))
            best_voice.notes.append(note)
            continue

        best_assignment: Optional[tuple[Voice, ...]] = None
        best_cost = float("inf")

        for assignment in permutations(voices, len(ordered)):
            cost = 0.0
            for index, (note, voice) in enumerate(zip(ordered, assignment)):
                cost += compute_voice_cost(note, voice)
                if index != 0 and voice is upper:
                    cost += OVERLAP_PENALTY
                if index == 0 and voice is lower:
                    cost += OVERLAP_PENALTY
            if cost < best_cost:
                best_cost = cost
                best_assignment = assignment

        for note, voice in zip(ordered, best_assignment):
            voice.notes.append(note)

    return [voice for voice in voices if voice.notes]


def split_instrument_voices(instrument: pretty_midi.Instrument, fallback_name: str) -> list[Voice]:
    """Split one source track into monophonic voices while preserving monophonic tracks."""
    notes = extract_notes(instrument)
    if not notes:
        return []

    source_name = instrument.name.strip() or fallback_name
    polyphony = estimate_max_polyphony(notes)

    if polyphony <= 1:
        return [Voice(name=source_name, notes=notes)]

    if polyphony == 2:
        return split_two_voice_notes(notes, source_name)

    voices = assign_voices(notes, max_voices=min(MAX_VOICES, polyphony))
    active = [voice for voice in voices if voice.notes]
    active.sort(key=lambda voice: voice.average_pitch, reverse=True)

    if len(active) == 2:
        labels = ["Upper", "Lower"]
    else:
        labels = [f"Part {index + 1}" for index in range(len(active))]

    for voice, label in zip(active, labels):
        voice.name = f"{source_name} {label}"

    return active


# ---------------------------------------------------------------------------
# Step 5: Export MIDI
# ---------------------------------------------------------------------------
def export_midi(
    voices: list[Voice],
    output_path: str,
    source_midi: pretty_midi.PrettyMIDI,
    original_program: int = 52,
) -> None:
    """
    Write a new MIDI file where each voice occupies its own instrument track.

    Timing and velocity are preserved.  The instrument program defaults to
    52 (choir aahs) but is overridden by the program found in the source file
    when available.
    """
    out = pretty_midi.PrettyMIDI(initial_tempo=_get_initial_tempo(source_midi))

    for voice in voices:
        if not voice.notes:
            continue
        instrument = pretty_midi.Instrument(
            program=original_program,
            name=voice.name,
        )
        for n in voice.notes:
            instrument.notes.append(
                pretty_midi.Note(
                    velocity=n.velocity,
                    pitch=n.pitch,
                    start=n.start,
                    end=n.end,
                )
            )
        out.instruments.append(instrument)

    out.write(output_path)


def _get_initial_tempo(midi: pretty_midi.PrettyMIDI) -> float:
    times, tempos = midi.get_tempo_changes()
    return float(tempos[0]) if len(tempos) > 0 else 120.0


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
def split_midi(input_path: str, output_path: str) -> None:
    """Full pipeline: load → split each track → export."""
    midi = load_midi(input_path)

    voices: list[Voice] = []
    original_program = 52  # choir aahs default
    program_set = False

    for index, instrument in enumerate(midi.instruments, start=1):
        if instrument.is_drum:
            continue
        if not program_set:
            original_program = instrument.program
            program_set = True
        voices.extend(split_instrument_voices(instrument, fallback_name=f"Track {index}"))

    if not voices:
        raise ValueError("No notes found in the input MIDI file.")
    export_midi(voices, output_path, midi, original_program)

    print(f"Split into {len(voices)} voice(s):")
    for v in voices:
        print(f"  {v.name}: {len(v.notes)} note(s)")
    print(f"Output written to: {output_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Split a MIDI file into separate SATB (+ optional solo) voice tracks.",
    )
    parser.add_argument("input", help="Input MIDI file path")
    parser.add_argument("output", help="Output MIDI file path")
    args = parser.parse_args()

    split_midi(args.input, args.output)


if __name__ == "__main__":
    main()
