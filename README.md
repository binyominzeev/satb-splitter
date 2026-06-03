# satb-splitter

Choir MIDI splitting to make it easier for each singer.

`split_satb_midi.py` is a Python CLI tool that processes a MIDI file containing
SATB choral music (and optionally a solo/extra voice) and separates polyphonic
content into independent monophonic voice tracks.

## Requirements

- Python 3.10+
- [pretty_midi](https://github.com/craffel/pretty-midi)

```
pip install -r requirements.txt
```

## Usage

```
python split_satb_midi.py input.mid output.mid
```

### What it does

1. Reads every instrument track in the input MIDI file.
2. Extracts all notes and sorts them by time (simultaneous notes are ordered
   highest-pitch first so they land in the correct SATB slot).
3. Uses a greedy cost-function algorithm to assign each note to a voice:
   - pitch distance from the voice's last note
   - time-gap continuity preference
   - soft SATB pitch-range compatibility penalty
   - hard overlap penalty (prevents two notes sounding simultaneously in the
     same voice)
4. Re-labels the resulting voice clusters as **Soprano**, **Alto**, **Tenor**,
   **Bass** (ordered by average pitch, high → low) and **Solo** for a 5th voice
   if one is detected.
5. Writes a new MIDI file where each voice is its own named instrument track.

### Voice detection

| Label   | Typical range |
|---------|---------------|
| Soprano | C4 – A5       |
| Alto    | G3 – D5       |
| Tenor   | C3 – G4       |
| Bass    | E2 – C4       |
| Solo    | detected dynamically as 5th cluster |

- If fewer than 4 voices are present the tool labels only what it finds.
- No notes are lost: every note from the input appears in exactly one output track.
