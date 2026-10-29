# Delta

The new Beta development project, starting from a standalone copy of Gamma.
Source: `D:\RAJKO\Projekti\Muzika\Beater.3\gamma`, copied on 2026-10-28.
Beta and Gamma remain unchanged. Delta imports no code from either project and
uses its own Python environment and `%LOCALAPPDATA%\Delta\delta.log`.

## Run

Double-click `launch.bat`.

For a fresh copy, run `setup.bat` once to create the local environment and install
the dependencies, then use `launch.bat`.

## Interface

The board follows `design/original-sketch.jpg`: Melody and Rhythm group headers
branch into horizontal lanes. Melody names open resizable editor windows; Rhythm names open lane options in the main window.
Each lane shows **one complete loop**, with its own moving playhead. Loop brackets
mark the beginning and end, circles show pitch changes, and vertical strokes show
rhythm hits. A live Melody or ambience lane shows one source-sound cycle. File names
and detailed playback information are kept in the editor's Options menu.

The **All lanes · shared timeline** overview below puts repeated loops on the same
time scale. A lane's name, loop, and overview markers use the same stable color.
**Combined** overlays them on one timeline; **Separate rows** gives each lane a
thin row using the same time axis. Switching views changes no playback or timing.
**Zoom** magnifies the sounds inside the shared timeline, with **200%** as the
default. Higher zoom spreads events farther apart in time and enlarges hit markers,
pitch shapes, and source waveforms. Choose **100%** to see the full alignment cycle.
Zoom does not change the panel height, sound, loop lengths, or playback positions.
The panel starts compact again. Its divider remains adjustable if needed.
Click a colored name in the overview to select its lane. The shared window follows
the master clock; at 100% three- and four-beat loops show their common twelve-beat cycle.

Delta opens with **zero lanes**, with permanent **Rhythm** and **Melody** sections.
Use the **+** button beside either heading to add and name a lane. Melody opens
its editor automatically; Rhythm stays in the main window. Click a Melody name to
open its editor and **Options** menu, or a Rhythm name to open its options directly.
Options include **Rename lane…**, sound loading, loop controls, volume, tempo, and removal.
Existing lanes keep playing while naming or adding another lane; their playback
positions and loop timings are preserved.
**Remove this lane** stops and releases that lane without changing the
others. Counts are flexible; the sketch does not prescribe a fixed combination.
Melody lanes use the Melody branch; rhythm lanes use Rhythm.

Visible **Load sound (Ctrl+O)** and **Enter** controls act on the selected lane,
with instructions that follow loading and rhythm recording.
Load any supported audio file, including an Alpha export. The Enter button and
physical Enter key perform the same action.

For Rhythm lanes (including kicks): hold **L** to start a loop, tap **Enter**
for each hit, then release **L** to set the out point and repeat automatically.
The loop includes any silence from L down to the first hit and from the last hit
to L up. There is no preset hit count, take count, or averaging. **Space** pauses
or resumes a completed loop. **Escape** cancels an unfinished recording; an
existing loop is restored if you were replacing it. Switching lanes or leaving
the app also cancels unfinished recording. Other lanes keep playing.
Load another sound using the folder button or **Ctrl+O**.
If the selected lane already contains a sound, the import creates a separate
lane of the same type, so an existing kick and its loop keep playing. Loading
into an empty lane fills that lane. To deliberately replace a sound, choose
**Replace this lane's sound…** in its lane options; that resets that
lane's recorded loop.

Melody still repeats its loaded sound with live Up/Down pitch control.
**Bass / pitch** slider in the editor's Options menu sets pitch directly from
**-24 to +24 semitones** in 0.1-semitone increments. Lower values deepen the sound;
0 restores the file's original pitch. With a pitch loop active, this sets the
loop's base tone and transposes **all notes together** without stopping playback
or changing the loop's timing, position, or intervals. The slider holds the base
value while the individual notes play. Its range narrows when necessary to keep
every note within the supported pitch range, preserving the intervals. Pitch
reset applies to the base while keeping the loop active. During L recording,
slider movements become part of the recorded pitch sequence.
An optional pitch sequence can be recorded by holding **L**, playing Up/Down (or Shift glides),
then releasing **L**. The pitches and their timing repeat inside that loop; each
cycle returns to the recorded starting pitch. The sound file continues playing
underneath the sequence. **Pitch loop** in the editor's Options menu enables or
disables the saved sequence without stopping the sound. Using Up/Down outside
recording also returns to live control; the saved sequence remains available.
**Space** pauses/resumes; **Escape** cancels a new recording and restores the
previous sequence. Melody's **L** is now recording; **Ctrl+O** loads a sound.

Pitch loops interpolate between their recorded points with a **Smooth** curve by
default. **Double-click the interval between two pitch points** in the loop editor
to switch just that interval to **Linear** progression; double-click it again to
restore Smooth. This changes the audible pitch movement as well as the drawing,
without stopping the loop, moving points, or changing its length. The closing
interval connects the last point back to the first at the loop boundary.
**Progression** in the editor's Options menu applies **Smooth**, **Linear**, or
the original **Steps** behavior to the whole loop and clears interval overrides.
The main loop and shared overview show the same progression that is played.
**Drag a melody point up or down** to change just that note's pitch while the loop
keeps playing. Its time and the other notes stay fixed; adjacent interpolation
updates to meet the new pitch. Hold **Shift** for finer adjustment. Double-click
the interval between points to change progression, rather than the points themselves.

## Melody editor windows and inline Rhythm

Each Melody lane has one reusable, nonmodal editor with a larger canvas and
horizontal pitch-height guides, similar to a piano roll. The guides carry no fixed
frequency labels and do not snap pitches; notes remain freely draggable.
Smooth/linear interval editing and whole-loop base-pitch transposition remain available.
Rhythm lanes stay in the main window, with one horizontal line for each lane's hits.
Click a Rhythm lane name for its options; there is no separate Rhythm window.

Multiple Melody windows can stay open. The active editor receives its lane's keyboard
controls. Closing a Melody editor only hides the window; its lane keeps playing.
Reopen it by clicking its name. Removing a Melody lane closes its editor and removes
that lane's sound; closing Delta closes all Melody editors.

## Kick master clock and beat lengths

The first loaded Rhythm sound becomes the kick master. It starts with a four-beat
loop at **120 BPM** (four quarter-note beats, the default 4/4 bar), with **zero
kick hits**. Press play to run the silent clock, then add only the kicks you want.
In another Rhythm lane's name menu, **Use as kick
master clock** changes the reference. The top bar shows the master, beat, and BPM;
master beat lines appear behind the lanes. The BPM control sets the clock tempo.

While the master plays, **Enter** adds a kick at the current position. You can also
**double-click anywhere inside a Rhythm timeline** to add a hit at that position,
including while paused. There is no snapping or redistribution. Each extra kick leaves
the existing positions and loop duration unchanged. Added hits are permanent,
including hits recorded with the **Add hits** option. Click a hit to highlight it,
then click **Delete hit** or press **Delete** to remove only that hit from the
repeating pattern. Deleting even every kick leaves the clock running.

**Beats per loop** in each lane's options sets the loop's length at the master tempo.
For example, a three-beat Rhythm loop cycles against a four-beat kick master and
they meet again every twelve beats. Hit count is independent of beat length.
Existing hits are kept at their positions when length changes; shortening a loop
hides events beyond its end, and extending it reveals them again. No hits are
silently deleted. Melody's control becomes available after recording a pitch loop.
The BPM control changes the timing of loops with a selected beat length, preserving
their musical positions; each lane's existing relative Tempo remains separate.

Recording a non-master loop with **L** selects **Free (L)**: its exact recorded
duration remains independent of the clock until you choose a beat length. Loops
with a chosen beat length start on the next shared master beat. Free loops start
immediately. Re-recording the master with L keeps its beat count and derives the
clock tempo from your recorded loop length, without analyzing the sound file.

Add or remove lanes as needed. See `GAMMA_CONTROLS.md` for inherited playback controls;
its original five-lane startup layout and rhythm count/take workflow have been
replaced by the empty board and hold-L recording described above.
That document is an unchanged reference from Gamma; its installer instructions
and Gamma-specific paths do not apply to Delta. No installer is included yet.
Sessions are not saved by this baseline version.

## Verify

```
.venv\Scripts\python.exe -m unittest test_delta test_clock test_pitch_curve
.venv\Scripts\python.exe verify_ui.py
```

These inherited checks cover the audio engine and offscreen UI. They do not
verify audible output on physical hardware.
