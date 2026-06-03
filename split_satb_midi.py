#!/usr/bin/env python3
"""
SATB + optional solo voice aware MIDI splitter.

Usage:
    python split_satb_midi.py input.mid output.mid
"""

import argparse
from dataclasses import dataclass, field
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


def assign_voices(notes: list[Note]) -> list[Voice]:
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
        if best_cost >= OVERLAP_PENALTY and len(voices) < MAX_VOICES:
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
    """Full pipeline: load → extract → assign → normalise → export."""
    midi = load_midi(input_path)

    all_notes: list[Note] = []
    original_program = 52  # choir aahs default
    program_set = False

    for instrument in midi.instruments:
        if instrument.is_drum:
            continue
        if not program_set:
            original_program = instrument.program
            program_set = True
        all_notes.extend(extract_notes(instrument))

    if not all_notes:
        raise ValueError("No notes found in the input MIDI file.")

    # Sort globally by start time, then pitch descending (handles chords)
    all_notes.sort(key=lambda n: (n.start, -n.pitch))

    voices = assign_voices(all_notes)
    voices = normalize_satb(voices)
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
