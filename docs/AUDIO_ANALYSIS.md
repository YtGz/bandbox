# 🧠 Audio Analysis — How BandBox Recognizes Your Riffs

> *Standard music analysis tools were built for pop and classical. They hear your band and give up. BandBox was built for the genres that break everything else.*

This document explains how BandBox identifies songs from raw practice recordings — even through heavy distortion, blast beats, alternate tunings, and partial takes. If you've ever wondered how a computer could tell your riffs apart the same way you do, read on.

---

## The Problem: Why Standard Tools Fail

Music Information Retrieval (MIR) has been a research field for decades. The standard toolkit — chroma features, pitch detection, chord recognition — works beautifully on acoustic music, pop, and clean recordings. It was not built for you.

**Distortion destroys harmonic clarity.** A clean guitar chord has a fundamental frequency and neat overtones. A distorted power chord generates dense intermodulation products across the entire spectrum. Standard pitch detectors (YIN, pYIN) expect clean harmonics — they choke on distortion and return garbage.

**Blast beats overwhelm everything.** At 200+ BPM with every instrument hitting every subdivision, the signal becomes a near-constant wall of energy. Standard beat trackers lock to the wrong tempo. Standard onset detectors fire on every sample. Standard energy-based segmentation sees no dynamics to segment.

**Tuning varies between sessions.** Drop D today, drop C tomorrow, half a step flat because nobody brought a tuner. Standard chroma features are key-dependent — the same riff in a different tuning looks like a completely different riff.

**Partial takes are common.** Sometimes you rehearse one riff for ten minutes, not the whole song. Standard "whole-song similarity" approaches need a full recording to compare. You need riff-level matching.

BandBox solves these problems by focusing on what *survives* distortion and what makes riffs recognizable to a human ear — even a heavily abused one.

---

## Core Insight: What Makes a Riff Recognizable?

Ask a metalhead to identify a song from a 5-second clip. They're not analyzing harmonic intervals or chord voicings. They're hearing:

1. **The rhythmic pattern** — *when* notes are hit. A syncopated groove feels completely different from straight 16ths, even if the notes are identical.
2. **The melodic shape** — *where* the pitch goes. Up, down, big jump, small step. The wave-like movement of a tremolo melody is instantly recognizable, even if you can't name the exact notes.
3. **The drum pattern** — kick and snare hits anchor a riff's identity. Change the drum pattern under the same guitar riff and it feels like a different song.

These three features — rhythm, melodic shape, and drums — are robust to distortion, tuning changes, and tempo drift. BandBox extracts all three and weights them adaptively based on what kind of riff it's listening to.

---

## Step 1: Harmonic-Percussive Source Separation (HPSS)

Before analyzing anything, BandBox splits the audio into two layers:

```
Raw recording
    │
    ├──→ Harmonic layer  (guitar sustain, bass, vocals)
    │
    └──→ Percussive layer (drums, pick attacks, transients)
```

This is done with [HPSS](https://librosa.org/doc/main/generated/librosa.effects.hpss.html) (Harmonic-Percussive Source Separation), a well-established technique that exploits a simple property: harmonic content appears as horizontal lines in a spectrogram (sustained pitches), while percussive content appears as vertical lines (sharp transients).

Why this matters:

- **Guitar pitch analysis** improves dramatically when drum blasts aren't contaminating the spectrum
- **Drum pattern extraction** gets a clean signal without guitar harmonics muddying the onsets
- **Both features become independently reliable** instead of a single noisy mess

This single preprocessing step is responsible for the biggest quality jump in the entire pipeline.

---

## Step 2: Melodic Contour — Tracking the Wave

This is where it gets interesting for black metal specifically.

### The Tremolo Picking Advantage

Tremolo picking — rapid alternate picking on single notes or dyads — is a defining technique of black metal. Counterintuitively, it's one of the *easiest* signals to analyze:

```
Tremolo riff: E-E-E-E-G-G-G-G-A-A-A-A-B-B-B-B-A-A-A-A-G-G-G-G

What you hear:   a wave of pitch rising and falling
What we track:   ▁▁▁▁▃▃▃▃▅▅▅▅▇▇▇▇▅▅▅▅▃▃▃▃
                   E     G     A     B     A     G

Quantized:       ════ ╱╱╱╱ ╱╱╱╱ ╱╱╱╱ ╲╲╲╲ ╲╲╲╲
                 flat  +2     +1    +1    -1    -2
                     (big rise)(small)(small)(big drop)
```

The interval sequence uses five levels — not just direction, but **magnitude**: `-2` (big drop), `-1` (small drop), `0` (flat), `+1` (small rise), `+2` (big rise). This matters because the *size* of a pitch jump is a core part of a riff's identity: a minor second oscillation (E→F→E) feels completely different from a perfect fourth jump (E→A→E), even though both go "up then down."

The rapid picking creates a sustained buzz at each pitch — almost like a bowed string. The note doesn't decay before you can measure it. The pitch *changes slowly* while the picking is fast, producing a smooth, wave-like melodic contour with clear directional movement.

This contour — the shape of the melody over time — is the primary fingerprint for tremolo riffs.

### How We Extract It

BandBox uses a three-method cascade on the harmonic layer, selecting the best result:

**Method 1: Spectral centroid (most robust, primary default).** The spectral centroid is the "center of mass" of the frequency spectrum. When the guitar moves to a higher note, the centroid shifts up — even through heavy distortion, because distortion preserves the *relative* frequency of the fundamental. Heavy smoothing (median filter → uniform filter) removes the tremolo ripple and reveals the underlying wave shape.

**Method 2: Spectral rolloff (backup).** The frequency below which 50% of spectral energy sits. Less precise than centroid in general, but more resistant to certain distortion artifacts — when distortion generates strong high-frequency harmonics that pull the centroid up, the rolloff at 50% stays anchored to the fundamental region. BandBox computes both centroid and rolloff, scores each by the quality of melodic movement (autocorrelation of the contour's derivative), and picks the winner.

**Method 3: pYIN (most precise, when it works).** Probabilistic pitch detection that can extract actual fundamental frequencies. Fails on heavily distorted full-band recordings, but works well on cleaner passages, isolated instruments, or bass-heavy sections. BandBox checks pYIN confidence and uses it when it's reliable (~50%+ voiced probability). When pYIN is confident, it wins over centroid and rolloff — it's the most accurate pitch representation, just the least robust to distortion.

### Making It Tuning-Independent

The same riff played in drop D and drop C has different absolute pitches but the *same shape*. BandBox normalizes every contour to zero mean and unit variance:

```
Drop D:   E2  G2  A2  B2  A2  G2   →  normalize  →  -1.2  -0.4  0.4  1.2  0.4  -0.4
Drop C:   D2  F2  G2  A2  G2  F2   →  normalize  →  -1.2  -0.4  0.4  1.2  0.4  -0.4
                                                       ↑ identical after normalization
```

The riff is recognized regardless of tuning. This also handles gradual detuning during a session (strings stretch, temperature changes).

### Making It Tempo-Independent

Every contour is resampled to a fixed length (200 points) regardless of how long the riff was played. Comparison uses Dynamic Time Warping (DTW) with open begin/end, which handles:

- **The same riff played at different tempos** — DTW stretches and compresses to find the best alignment
- **Partial takes** — open begin/end means a 10-second excerpt can match against a full 30-second riff (subsequence matching)
- **Natural timing variation** — musicians aren't metronomes; DTW tolerates drift

---

## Step 3: Rhythm Fingerprinting — The Groove

For groove sections, breakdowns, and mid-tempo riffs, the melodic contour is less distinctive — it might be the same power chord chugged in different rhythmic patterns. Here, *when* notes are hit matters more than *where* the pitch goes.

### Beat-Aligned Onset Patterns

BandBox uses [madmom](https://madmom.readthedocs.io/en/latest/)'s neural beat tracker (robust up to 260 BPM) to find the beat grid, then captures what happens within each beat:

1. Detect all onsets (note attacks) in the full signal
2. Align onsets to the beat grid
3. Subdivide each beat into 16 slots
4. Record the onset strength in each slot
5. Average across all beats → **the groove pattern**

```
Groove riff:     X . . X . . X X . . X . . . X .
16-slot pattern: [1 0 0 1 0 0 1 1 0 0 1 0 0 0 1 0]
                  ↑ this IS the riff's rhythmic identity

Blast beat:      X X X X X X X X X X X X X X X X
16-slot pattern: [1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1]
                  ↑ everything is on — rhythm is uniform,
                    not useful for identification
```

The same process runs separately on the **percussive layer** (drums only), producing a dedicated drum groove pattern. Kick/snare patterns are the most consistent element across takes — the drummer plays the same pattern even when the guitarists mess up.

### Why This Survives Distortion

Distortion doesn't change *when* you hit a note — only *how it sounds*. The onset (the transient of the pick attack) punches through even the heaviest distortion. A distorted power chord still has a sharp attack, and that attack happens at the same moment in the rhythmic pattern regardless of gain, tuning, or EQ.

---

## Step 4: Adaptive Weighting — Blast vs Groove

Here's the key insight: **different types of riffs need different analysis strategies.**

A groove riff has distinctive rhythm but boring pitch movement (often a single chugged note). A tremolo riff has distinctive pitch movement but boring rhythm (constant 16ths). Using the same feature weights for both throws away the most useful signal in each case.

BandBox detects the riff type automatically by measuring **onset uniformity** — the coefficient of variation of the groove pattern:

```
Uniform (blast/tremolo):  [.9 .9 .8 .9 .9 .8 .9 .9]  →  low variance  →  lean on CONTOUR
Sparse (groove/slam):     [1. 0  0  .8 0  0  1. .7 ]  →  high variance →  lean on RHYTHM
```

| Riff type | Contour | Groove | Drums | Spectral | Tempo |
| --- | ---: | ---: | ---: | ---: | ---: |
| Blast beat / tremolo | **55%** | 10% | 10% | 5% | 20% |
| Groove / breakdown | 15% | **35%** | **20%** | 10% | 20% |
| Mixed / unclear | 30% | 25% | 15% | 10% | 20% |

This means BandBox recognizes riffs the same way you do: for blasty parts, it follows the melody; for groovy parts, it follows the rhythm. Automatically, per riff.

---

## Step 5: Riff Segmentation — Breaking Songs Into Parts

A song is a sequence of riffs. Before comparing anything, BandBox segments each recording into riff-sized chunks using novelty detection on a self-similarity matrix (SSM).

The process starts by extracting spectral contrast and onset strength for each time frame — a feature combination that captures both tonal character and rhythmic character, which is more robust for distorted music than chroma features alone. These features form a matrix that is compared against itself to produce the SSM, where each cell represents the similarity between two points in time:

```
Song structure: A A B B A C A

SSM:  A  A  B  B  A  C  A
  A  ██ ██ ░░ ░░ ██ ░░ ██
  A  ██ ██ ░░ ░░ ██ ░░ ██
  B  ░░ ░░ ██ ██ ░░ ░░ ░░
  B  ░░ ░░ ██ ██ ░░ ░░ ░░
  A  ██ ██ ░░ ░░ ██ ░░ ██
  C  ░░ ░░ ░░ ░░ ░░ ██ ░░
  A  ██ ██ ░░ ░░ ██ ░░ ██

Novelty peaks at: A→B, B→A, A→C, C→A boundaries
```

Repeating sections appear as bright blocks on the diagonal. A [checkerboard kernel](https://www.audiolabs-erlangen.de/resources/MIR/FMP/C4/C4S2_SSM.html) — a matrix of +1s and -1s in a checkerboard pattern — slides along the diagonal and fires at transitions, producing a novelty curve. Peaks in this curve mark where the music changes character: a riff transition. These peaks become segment boundaries. Short segments (<3 seconds) are merged with their neighbors; long segments (>60 seconds) are split.

This per-riff approach is what enables partial take matching. Recording just one riff from a song? It matches against every occurrence of that riff across every recording in the library.

---

## Step 6: Matching — Finding the Same Riff Across Takes

With fingerprints extracted for every riff in every recording, BandBox runs a brute-force comparison: every new riff against every existing riff.

For each pair, the adaptive weighting produces a single similarity score (0–1) with a per-feature breakdown. Tempo is scored as a full feature (not just a penalty), with a key refinement: **double/half tempo detection**. Beat trackers sometimes lock to half or double the actual tempo (especially common with blast beats where the tracker hears the snare on 2 and 4 as the beat). BandBox checks all three ratios — direct, double, and half — and uses the best match, so a riff tracked at 100 BPM and the same riff tracked at 200 BPM still score high.

### Subsequence Matching for Partial Takes

Standard DTW requires two sequences of similar length. BandBox uses **open-begin/open-end DTW**, which allows a short sequence to match against part of a longer one:

```
Full take:    [intro] [riff A] [riff B] [riff A] [riff B] [outro]
Partial take:          [riff A]

DTW (open begin/end):
  ✅ Partial take's riff A matches against BOTH occurrences
     of riff A in the full take
```

This means a 30-second recording of just the main riff correctly identifies the song, even if the full version is 5 minutes long with six different sections.

### Riff Overlap → Song Identity

After pairwise riff matching, BandBox builds a picture of how two recordings relate:

| Overlap | Interpretation |
| --- | --- |
| Multiple riffs match, high coverage | Almost certainly the same song |
| One strong riff match | Same song (partial take) or shared riff between songs |
| No significant matches | Different songs |

The final grouping decision is made by the LLM (see [Implementation Guide](IMPLEMENTATION.md)), which triangulates audio similarity with speech transcripts and between-recording transitions to produce song groups with working titles.

---

## Why It Works Like a Metalhead's Ear

BandBox doesn't try to understand music theory. It doesn't know what a diminished chord is or what time signature you're in. Instead, it captures the same features that let *you* recognize a song from a 3-second clip in a noisy practice room:

| What you hear | What BandBox measures |
| --- | --- |
| "That's the tremolo melody that goes up then drops" | Spectral centroid contour via HPSS, normalized and smoothed |
| "That's the syncopated groove riff" | Beat-aligned 16-slot onset pattern |
| "Same riff, different tuning" | Zero-mean unit-variance normalization removes absolute pitch |
| "Same riff, we played it faster this time" | DTW stretches time to find the best alignment |
| "They're just practicing the intro riff" | Open-begin/open-end subsequence matching |
| "The drums are doing that same double-kick thing" | Percussive layer onset pattern after HPSS |
| "It's a blast beat part — I recognize the guitar melody" | Adaptive weighting shifts to contour when onsets are uniform |

No clean audio required. No genre assumptions. No training data needed on day one.

---

## Limitations — What It Can't Do (Yet)

**Warm-up scales that happen to be rhythmic.** If someone plays a scale with even timing before the song, the trim detector might include it. The [trim review UI](IMPLEMENTATION.md) lets you fix these cases.

**Two songs with identical riffs.** If you literally reuse a riff in a different song (with different surrounding riffs), BandBox will flag a riff-level match but might not know which song it belongs to. Speech transcripts and structural context (which riffs appear together) usually resolve this.

**Single sustained notes with long gaps.** A very sparse, ambient intro (one note every 5 seconds) doesn't produce enough onsets for rhythm analysis or enough pitch movement for contour tracking. The energy wall detector usually catches when the full band enters, but the quiet intro might get trimmed.

**Accuracy without real-world tuning.** The feature weights and thresholds are informed by MIR research and the characteristics of the genre, but they haven't been tuned on *your specific recordings* yet. Expect some iteration. Every correction you make in the web UI improves future grouping.

---

## Future: Learning Your Band's Sound

The fingerprinting approach works from day one with no training data. But BandBox is designed to get smarter over time.

Every manual correction — moving a recording to a different song, confirming a grouping, creating a new song — is logged in the `corrections` table. Once enough confirmed groupings exist (~20+ songs with multiple takes each), a contrastive learning model can be trained:

- **Anchor**: a riff from song X
- **Positive**: another riff from song X (confirmed same song)
- **Negative**: a riff from song Y (confirmed different song)

The model learns to map riffs into an embedding space where same-song riffs cluster together and different-song riffs separate. This trained model runs alongside the fingerprinting system, and its learned features capture patterns specific to your band that no generic algorithm could.

This is future work — the data model supports it, and the corrections are being collected from day one.

---

## References

The individual techniques used in BandBox are well-established in Music Information Retrieval research:

- **HPSS**: Fitzgerald, D. (2010). *Harmonic/Percussive Separation Using Median Filtering*
- **DTW for music similarity**: Serra, J. et al. (2009). *Cross Recurrence Quantification for Cover Song Identification*
- **Self-similarity matrices**: Foote, J. (1999). *Visualizing Music and Audio Using Self-Similarity*
- **Neural beat tracking**: Böck, S. et al. (2016). *Joint Beat and Downbeat Tracking with Recurrent Neural Networks* (madmom)
- **Spectral contrast**: Jiang, D. et al. (2002). *Music Type Classification by Spectral Contrast Feature*
- **Effects of distortion on guitar harmonics**: Herbst, J. (2019). *Influence of Distortion on Guitar Chord Structures*

The specific combination — HPSS preprocessing, adaptive contour/rhythm weighting based on onset uniformity, and riff-level subsequence matching for rehearsal recording grouping — is a novel application assembled for this project.
