# satb-splitter

Choir MIDI splitting to make it easier for each singer.

`split_satb_midi.py` is a Python CLI tool that processes a MIDI file containing
SATB choral music (and optionally a solo/extra voice) and separates polyphonic
content into independent monophonic voice tracks while preserving source tracks
that are already monophonic.

## Requirements

- Python 3.9+
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
2. Keeps already monophonic tracks intact.
3. Splits only the tracks that contain overlapping notes:
   - two-voice tracks are separated into stable `Upper` and `Lower` parts
   - denser tracks fall back to the greedy cost-function assignment
4. Writes a new MIDI file where each resulting voice is its own named
   instrument track.

### Voice detection

| Label   | Typical range |
|---------|---------------|
| Soprano | C4 – A5       |
| Alto    | G3 – D5       |
| Tenor   | C3 – G4       |
| Bass    | E2 – C4       |
| Solo    | detected dynamically as 5th cluster |

- Existing solo or chant tracks stay separate if they are already monophonic.
- No notes are lost: every note from the input appears in exactly one output track.
