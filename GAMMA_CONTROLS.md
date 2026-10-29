# Gamma

A separate, sample-based performance prototype. No Beta modules are imported or modified.

Run `launch.bat`. It uses Gamma's virtual environment if present, otherwise the existing
Beta Python environment (dependencies only), then system Python. To install independently:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Playing

Load a mono or stereo sound using each lane's folder button. Select a lane by its name.
WAV, FLAC, AIFF, OGG and decoder-supported MP3 files can be loaded. Nothing analyzes
tempo, key, or rhythm. Empty lanes stay silent. Microphone recording and AI generation
are external to Gamma: load the recordings they produce.

For Kick and Impulse:

1. Choose 2, 3, or 4 takes in the lane menu. Both lanes default to 3.
2. Tap the physical key below Escape, left of 1, once per desired hit (1 to 128).
3. Press Escape to confirm the hit count.
4. Tap Enter through the same rhythm for each take. The tap that closes one take
   also plays the first hit of the next. Keep tapping continuously across that join.
5. Stop tapping after the last sound of the final take. No closing tap is needed.
   Gamma averages first/internal hit positions across all attempts. With the default
   three attempts, the second attempt anchors the loop start, and the time from the
   second start to the third start defines the loop length. Playback rejoins that
   timing at the next loop start after the final sound, without doubling it.
   With two or four attempts, completed intervals are averaged as before.
   No BPM or grid snapping is involved. If a very late internal hit would average
   beyond the retained last hit, its position is limited to that last-hit position.

The lane shows the current take and hit progress. Five hits over three takes need
15 taps total. One hit over four takes needs four taps: the three measured intervals
are averaged into one repeating interval. Take count is locked during recording; Escape or the
circular-arrow button discards all partial takes and starts a fresh recording with
the same sound, hit count, take count, and volume. Stopping during recording also
discards partial takes. Changing take count after playback applies to the next recording.

The circular-arrow icon beside the count, or Escape after recording, prepares a
new take while keeping the loaded sound and hit count. Tap Enter immediately to
record the replacement rhythm. To choose a different hit count, start tapping
the key below Escape again; that starts a fresh count. Loops have independent lengths;
they may drift against each other. Space or the play button pauses/resumes the
selected lane. The restart icon starts that lane from the beginning. Stop in the
top bar stops everything; recorded rhythm patterns remain available.

Bass and Melody repeat their loaded sound while playing. Enter or Space toggles
playback. Each lane's menu chooses the Up/Down jump: Semitone (1), Minor third (3),
Major third (4), or Fifth (7 semitones). Each press moves relative to the current
pitch. The default remains one semitone. Bass keeps smooth
transitions: it covers about 95% of a pitch change in 105 milliseconds while playing.
Its graph follows that actual pitch movement. Shift + held Up/Down still glides
at three semitones per second on Bass and six on Melody, regardless of the selected jump size.
Pitch range is +/-24 semitones relative to the file.
The reset icon restores its original pitch. Melody starts one octave up (+12 st),
while Bass starts at the source pitch. Pitch changes use playback rate, so
duration changes too. These lanes have identical controls; the pitch offset is
shown explicitly, and there is no key detection or automatic note selection.
Recording/looping an entire pitch performance is deferred; only the source sound
repeats. No synthesis, filters, effects, or sound-editing panel.

Ambience plays its recording once, or repeats when its Repeat checkbox is enabled.
Its waveform shows position in the complete recording. Kick and Impulse share a
12-second time window (four seconds behind, eight ahead); white lines mark now.
Bass and Melody show recent pitch movement on the same time scale. Ambience uses
its own full-recording scale, not that 12-second scale.

## Keyboard shortcuts

| Keys | Action |
| --- | --- |
| A / K / I / B / M | Select Ambience / Kick / Impulse / Bass / Melody |
| V / C | Selected lane volume up / down, 1 percentage point |
| X / Z | Selected lane relative tempo up / down, 0.1 percentage point |
| L | Load a sound into the selected lane |
| P | Pause / resume the selected lane |
| Hold R + L | Re-record the selected rhythm lane, keeping its settings |
| Hold R + I | Select Impulse and re-record it |
| Hold R + A | Select Ambience and restart the track |
| Hold R + B | Select Bass and restore original pitch |
| Hold R + M | Select Melody and restore original pitch |

Hold V/C/X/Z for repeated adjustments. For R combinations, hold R first, press
the second key, then release. File dialogs and open menus retain their normal
keyboard controls. The existing Enter, Escape, count key, Space, and pitch arrows
continue to work.

## Limits and recovery

Loading happens off the UI/audio thread. File limits: 192 MB decoded per recording,
384 MB per session, mono or stereo. The mixer has 48 voices and fixed headroom;
overload is clipped to the output range. All sounds use a fixed 35% mix gain.
Each lane has a vertical volume slider to the right of its graph, from 0% (silent)
to 100% (the previous fixed level). Changes are smoothed briefly to avoid abrupt
gain jumps. A silent lane keeps its timing and playback position. Loading another
sound or re-recording a loop keeps the lane's volume. System volume sets overall
listening level.

Beside each volume slider is a relative Tempo slider, 50-150%, centered at 100%.
Dragging adjusts gently by 0.1 percentage point per pixel; hold Shift for 0.01.
The mouse wheel also adjusts by 0.1 point, or 0.01 with Shift. Clicking the
track does not cause a sudden tempo jump. The display shows two decimal places.
Kick and Impulse change their hit spacing and loop duration without changing the
sample pitch or the original recorded timing. Live adjustments retain the current
position within the loop; paused loops retain their relative position as well.
For Ambience, Bass, and Melody this controls sample playback speed, so pitch also
changes, in addition to the arrow-key pitch setting. This is not pitch-preserving
time stretching. There is no BPM detection. Return to 100% for original speed.
Each lane keeps its tempo setting when loading a sound or re-recording; tapping
always captures your unscaled timing, and the tempo setting applies on replay.

ASIO support is enabled before loading the audio library, following the
[sounddevice setup documentation](https://github.com/spatialaudio/python-sounddevice/blob/master/doc/installation.rst).
Select ASIO4ALL in the output picker, choose the named stereo pair (1/2 or 3/4),
then use the connect icon next to the output picker. ASIO panel opens the installed
driver settings. The refresh icon beside that button rescans devices after changes.
Selecting a different output or pair disconnects playback until you connect it.
Gamma tries the device's native sample rate, then 48/44.1 kHz as needed, without
switching to a different output. Stored loop timings retain their duration.

Select an output and use the reconnect icon if audio is unavailable. Reconnecting
stops playback but keeps loaded sounds and completed patterns. Errors appear in
the footer; unexpected errors are logged in `%LOCALAPPDATA%\Gamma\gamma.log`. Keyboard auto-repeat is
ignored for taps, and losing focus releases glide controls.

This first version does not save sessions. Closing the app loses recorded patterns.
Opening an output stream alone does not establish that the intended speakers work;
audible testing on the actual laptop is still necessary.

## Windows installer

The installer targets Windows 10/11 x64 and bundles Python and its dependencies.
It installs for the current user, adds a Start menu shortcut and an optional desktop
shortcut, and provides an uninstaller. ASIO drivers must already be installed on
the destination computer if ASIO output is wanted.

To rebuild, create `.build-venv` with 64-bit Python and install
`requirements-build.txt`. Install Inno Setup 6.7.3 or newer, then run
`./build-installer.ps1 -Iscc 'C:\path\to\ISCC.exe'`. The script prompts for the
installation password without echoing it. Alternatively, provide it through the
`GAMMA_INSTALLER_PASSWORD` environment variable. Never commit the password.
The installer uses Inno Setup's password prompt and payload encryption.

Upload `release/Gamma-0.1.0-Windows-x64-Setup.exe` and `release/SHA256SUMS.txt`
as GitHub Release assets, or share the setup executable with your Patreon members.
Share the password separately. Do not upload the `dist`
folder if you want downloads to require that password. The password controls
installation; it does not prevent someone who has installed Gamma from copying it.
The installer is unsigned, so Windows may show an unknown-publisher warning.

### Add hits to an existing rhythm

On Kick or Impulse, enable **Add hits**, select the lane, and tap Enter while
the loop plays. Each tap adds a hit at that loop position for future cycles,
keeping its length and timing. Unchecking Add hits removes the temporary additions
and restores the original pattern without stopping playback. Paused loops
do not accept taps; re-recording, recounting, loading a sound, or Stop all
turns Add hits off. Temporary added hits count toward the 128-hit maximum while Add hits is enabled.
