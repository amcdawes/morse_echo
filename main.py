from nicegui import ui
import numpy as np
import sounddevice as sd
import random
import time
from datetime import datetime
import argparse

# Morse code mapping
MORSE_CODE = {
    'A': '.-', 'B': '-...', 'C': '-.-.', 'D': '-..', 'E': '.',
    'F': '..-.', 'G': '--.', 'H': '....', 'I': '..', 'J': '.---',
    'K': '-.-', 'L': '.-..', 'M': '--', 'N': '-.', 'O': '---',
    'P': '.--.', 'Q': '--.-', 'R': '.-.', 'S': '...', 'T': '-',
    'U': '..-', 'V': '...-', 'W': '.--', 'X': '-..-', 'Y': '-.--',
    'Z': '--..'
}

class MorseGame:
    def __init__(self, debug=False):
        # Debug mode
        self.debug = debug
        
        # Audio parameters
        self.sample_rate = 44100
        self.dot_duration = 0.1
        self.dash_duration = self.dot_duration * 3
        self.frequency = 800
        
        # Configure sounddevice for lower latency
        sd.default.latency = 'low'
        sd.default.blocksize = 2048  # Larger blocksize for stability
        sd.default.dtype = 'float32'

        # Session state
        self.session_active = False
        self.session_start_time = None
        self.session_length = 60  # seconds, default 1 min
        self.session_timer = None

        # Game state
        self.current_char = None
        self.play_time = None
        self.scores = []  # (played_char, time, correct, pressed_char)
        self.best_score = float('inf')
        self._is_playing = False
        self._char_generation = 0  # Incremented each time we play a new character

        # UI elements
        self.score_list = None
        self.status_label = None
        self.start_button = None
        self.stop_button = None
        self.length_input = None
        self.avg_history = []  # list of average response times after each correct answer
        self._replay_timer = None

        self.create_ui()
    
    def log(self, message):
        """Print debug message if debug mode is enabled."""
        if self.debug:
            timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]
            print(f"[{timestamp}] {message}")
    
    def create_ui(self):
        with ui.column().classes('w-full items-center justify-center'):
            ui.label('Morse Code Trainer').classes('text-2xl mb-4')
            # Session controls: put the length input above the buttons and make it wider
            with ui.row().classes('gap-4 mb-4'):
                with ui.column():
                    self.length_input = ui.number(label='Session Length (seconds)', value=60, min=10, max=600).classes('w-64')
                    with ui.row().classes('gap-2 mt-2'):
                        self.start_button = ui.button('Start', on_click=self.start_session).classes('w-32')
                        self.stop_button = ui.button('Stop', on_click=self.stop_session).classes('w-32')
            self.status_label = ui.label('Press Start to begin').classes('text-gray-600')
            ui.label('Recent Results:').classes('mt-4')
            # Results and chart area
            with ui.row().classes('items-start gap-6'):
                with ui.column().classes('w-96'):
                    self.score_list = ui.html('', sanitize=False).classes('h-48 overflow-y-auto font-mono pr-4')
                with ui.column().classes('w-96'):
                    ui.label('Average response time').classes('mb-2')
                    # placeholder for SVG chart; update in update_ui
                    self.chart = ui.html('', sanitize=False).classes('w-96')
    
    def update_ui(self):
        # Update score history
        def render_row(item):
            played_char, t, correct, pressed = item
            if correct:
                return f'<span style="color: green">{"{:.2f}s".format(t)} - {played_char}</span>'
            else:
                return f'<span style="color: red">ERROR - played: {played_char}, pressed: {pressed}</span>'

        scores_html = '<br>'.join(
            render_row(item) for item in reversed(self.scores[-20:])
        )
        self.score_list.content = f'<div style="white-space: pre-wrap">{scores_html}</div>'
        if not self.session_active:
            self.status_label.text = 'Press Start to begin'
            self.status_label.classes('text-gray-600')

        # Update average response time chart (simple SVG sparkline)
        if self.avg_history:
            width = 360
            height = 120
            padding = 8
            vals = self.avg_history[-40:]
            max_v = max(vals) if vals else 1.0
            min_v = min(vals) if vals else 0.0
            span = max_v - min_v if max_v > min_v else 1.0
            pts = []
            for i, v in enumerate(vals):
                x = padding + i * (width - 2 * padding) / max(1, len(vals) - 1)
                y = padding + (height - 2 * padding) * (1 - (v - min_v) / span)
                pts.append(f"{x:.1f},{y:.1f}")
            poly = ' '.join(pts)
            svg = f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">'
            svg += f'<rect x="0" y="0" width="{width}" height="{height}" fill="#fff" stroke="#eee"/>'
            svg += f'<polyline fill="none" stroke="#2b8bdb" stroke-width="2" points="{poly}" />'
            # draw last value
            last = vals[-1]
            last_x = padding + (len(vals) - 1) * (width - 2 * padding) / max(1, len(vals) - 1)
            last_y = padding + (height - 2 * padding) * (1 - (last - min_v) / span)
            svg += f'<circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="3" fill="#2b8bdb" />'
            svg += '</svg>'
            self.chart.content = svg
        else:
            self.chart.content = '<div style="color: #666">No data yet</div>'
    
    def generate_tone(self, duration):
        """Generate a tone with smooth attack/release envelope to reduce clicks."""
        samples = int(self.sample_rate * duration)
        t = np.linspace(0, duration, samples, False)
        tone = np.sin(2 * np.pi * self.frequency * t)
        
        # Apply gentle envelope to prevent clicks at start/end
        envelope_samples = int(self.sample_rate * 0.005)  # 5ms attack/release
        envelope = np.ones(samples)
        
        # Attack
        if samples > envelope_samples:
            envelope[:envelope_samples] = np.linspace(0, 1, envelope_samples)
            # Release
            envelope[-envelope_samples:] = np.linspace(1, 0, envelope_samples)
        
        return (tone * envelope * 0.3).astype(np.float32)  # Reduce volume and ensure float32
    
    def play_morse(self, morse_sequence):
        """Generate and play morse code sequence with proper timing."""
        signals = []
        gap = np.zeros(int(self.dot_duration * self.sample_rate * 0.5), dtype=np.float32)
        
        for symbol in morse_sequence:
            if symbol == '.':
                signals.append(self.generate_tone(self.dot_duration))
            elif symbol == '-':
                signals.append(self.generate_tone(self.dash_duration))
            signals.append(gap)
        
        # Concatenate all signals and ensure they're contiguous in memory
        signal = np.concatenate(signals).astype(np.float32)
        
        # Add small padding at the end to prevent cutoff
        padding = np.zeros(int(self.sample_rate * 0.05), dtype=np.float32)
        signal = np.concatenate([signal, padding])
        
        # Play with blocking to ensure complete playback
        sd.play(signal, self.sample_rate, blocking=True)
        sd.wait()  # Ensure playback is fully completed

    def play_bell(self, duration: float = 0.35, frequency: int = 1000):
        """Play a short bell sound (decaying sine) to mark session end."""
        samples = int(self.sample_rate * duration)
        t = np.linspace(0, duration, samples, False)
        # exponential decay envelope
        envelope = np.exp(-6 * t)
        tone = (np.sin(2 * np.pi * frequency * t) * envelope * 0.45).astype(np.float32)
        sd.play(tone, self.sample_rate, blocking=True)
        sd.wait()  # Ensure playback completes
    
    def play_morse_and_reset_timer(self, morse_sequence):
        if self._is_playing:
            self.log("play_morse_and_reset_timer: Already playing, skipping")
            return
        self._is_playing = True
        self.play_time = datetime.now()
        # Increment generation for this new character
        self._char_generation += 1
        current_generation = self._char_generation
        self.log(f"play_morse_and_reset_timer: Playing morse for '{self.current_char}', play_time={self.play_time}, generation={current_generation}")
        self.play_morse(morse_sequence)
        self._is_playing = False
        # schedule a one-shot replay after 1.5s if no answer
        try:
            # cancel previous scheduled replay if any
            if self._replay_timer is not None:
                self.log("play_morse_and_reset_timer: Cancelling previous replay timer")
                try:
                    self._replay_timer.stop()
                except Exception:
                    pass
                self._replay_timer = None
        finally:
            # schedule new one-shot timer with lambda to capture the current generation
            self.log(f"play_morse_and_reset_timer: Scheduling replay timer for 1.5s (generation={current_generation})")
            self._replay_timer = ui.timer(1.5, lambda: self._replay_if_still_waiting(current_generation), once=True)

    def _cancel_replay_timer(self):
        if self._replay_timer is not None:
            self.log("_cancel_replay_timer: Cancelling replay timer")
            try:
                self._replay_timer.stop()
            except Exception:
                pass
            self._replay_timer = None
        else:
            self.log("_cancel_replay_timer: No replay timer to cancel")

    def _replay_if_still_waiting(self, expected_generation):
        # Called by the one-shot timer after 1.5s; play again only if session still waiting for input
        self.log(f"_replay_if_still_waiting: Called (session_active={self.session_active}, current_char={self.current_char}, play_time={self.play_time}, expected_gen={expected_generation}, current_gen={self._char_generation})")
        
        # Clear the timer reference immediately to prevent double-firing
        self._replay_timer = None
        
        # Check if this timer is for the current character generation
        if expected_generation != self._char_generation:
            self.log(f"_replay_if_still_waiting: Stale timer (expected gen {expected_generation}, current gen {self._char_generation}), ignoring")
            return
        
        if not self.session_active or self.current_char is None or self.play_time is None:
            self.log("_replay_if_still_waiting: Conditions not met, skipping replay")
            return
        
        # ensure enough time has actually elapsed
        elapsed = (datetime.now() - self.play_time).total_seconds()
        self.log(f"_replay_if_still_waiting: Elapsed={elapsed:.3f}s, _is_playing={self._is_playing}")
        
        if elapsed >= 1.5 and not self._is_playing:
            # Set playing flag to prevent concurrent replays
            self._is_playing = True
            morse = MORSE_CODE[self.current_char]
            self.log(f"_replay_if_still_waiting: Replaying morse for '{self.current_char}'")
            self.play_morse(morse)  # Play once without rescheduling another replay timer
            self._is_playing = False
        else:
            self.log("_replay_if_still_waiting: Not replaying (too soon or already playing)")
    
    
    
    def next_char(self):
        self.current_char = random.choice(list(MORSE_CODE.keys()))
        self.log(f"next_char: Selected '{self.current_char}'")
        morse = MORSE_CODE[self.current_char]
        self.play_morse_and_reset_timer(morse)
        self.status_label.text = 'Listening...'
        self.status_label.classes('text-blue-600')
        self.update_ui()
    
    def handle_keypress(self, e):
        if not self.session_active or self.current_char is None or self.play_time is None:
            self.log(f"handle_keypress: Ignoring key '{e.key}' (session_active={self.session_active}, current_char={self.current_char}, play_time={self.play_time})")
            return
        if len(str(e.key)) == 1:
            pressed_char = str(e.key).upper()
            reaction_time = (datetime.now() - self.play_time).total_seconds()
            self.log(f"handle_keypress: Key '{pressed_char}' pressed, expected '{self.current_char}', reaction_time={reaction_time:.3f}s")
            if pressed_char == self.current_char:
                self.scores.append((self.current_char, reaction_time, True, pressed_char))
                self.best_score = min(self.best_score, reaction_time)
                # update average history
                correct_times = [t for (_, t, c, _) in self.scores if c]
                avg = sum(correct_times) / len(correct_times) if correct_times else 0.0
                self.avg_history.append(avg)
                self.status_label.text = f'Correct! ({self.current_char})'
                self.status_label.classes('text-green-600')
                self.log(f"handle_keypress: CORRECT answer")
            else:
                self.scores.append((self.current_char, reaction_time, False, pressed_char))
                self.status_label.text = f'Incorrect: you pressed {pressed_char}, expected {self.current_char}'
                self.status_label.classes('text-red-600')
                self.log(f"handle_keypress: INCORRECT answer")
            # clear current prompt and cancel any scheduled replay
            self.log(f"handle_keypress: Clearing current_char and play_time, cancelling replay timer")
            self.current_char = None
            self.play_time = None
            self._cancel_replay_timer()
            self.update_ui()
            # Move to next character if session is still active
            if self.session_active:
                self.log("handle_keypress: Scheduling next character in 0.5s")
                ui.timer(0.5, self.next_char, once=True)
    def start_session(self):
        if self.session_active:
            return
        self.session_length = int(self.length_input.value)
        self.session_active = True
        self.scores.clear()
        self.session_start_time = time.time()
        self.log(f"start_session: Starting session for {self.session_length}s")
        self.status_label.text = 'Session started!'
        self.status_label.classes('text-blue-600')
        self.update_ui()
        # buffer 1 second before first character
        ui.timer(1.0, self.next_char, once=True)
        # End session after specified time
        self._session_timer = ui.timer(self.session_length, self.stop_session, once=True)

    def stop_session(self):
        if not self.session_active:
            return
        self.log("stop_session: Stopping session")
        self.session_active = False
        self.current_char = None
        self.play_time = None
        # Cancel any pending replay timer
        self._cancel_replay_timer()
        # Cancel session timer if running
        if hasattr(self, '_session_timer') and self._session_timer is not None:
            try:
                self._session_timer.stop()
            except Exception:
                pass
            self._session_timer = None
        # Play a bell to indicate session end
        try:
            self.play_bell()
        except Exception:
            # if audio fails, just continue
            pass
        self.status_label.text = 'Session stopped.'
        self.status_label.classes('text-gray-600')
        self.update_ui()


# Parse command-line arguments
parser = argparse.ArgumentParser(description='Morse Code Echo Game')
parser.add_argument('--debug', action='store_true', help='Enable debug logging to console')
args = parser.parse_args()

# Create game instance and set up keyboard handler
game = MorseGame(debug=args.debug)
ui.keyboard(on_key=game.handle_keypress)

# Start the app
ui.run(title='Morse Code Echo Game')