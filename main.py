from nicegui import ui
import numpy as np
import sounddevice as sd
import random
import time
from datetime import datetime

# Morse code mapping
MORSE_CODE = {
    'A': '.-', 'B': '-...', 'C': '-.-.', 'D': '-..', 'E': '.',
    'F': '..-.', 'G': '--.', 'H': '....', 'I': '..', 'J': '.---',
    'K': '-.-', 'L': '.-..', 'M': '--', 'N': '-.', 'O': '---',
    'P': '.--.', 'Q': '--.-', 'R': '.-.', 'S': '...', 'T': '-',
    'U': '..-', 'V': '...-', 'W': '.--', 'X': '-..-', 'Y': '-.--',
    'Z': '--..', ' ': ' '
}

class MorseGame:
    def __init__(self):
        # Audio parameters
        self.sample_rate = 44100
        self.dot_duration = 0.1
        self.dash_duration = self.dot_duration * 3
        self.frequency = 800

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

        # UI elements
        self.score_list = None
        self.status_label = None
        self.start_button = None
        self.stop_button = None
        self.length_input = None
        self.avg_history = []  # list of average response times after each correct answer
        self._replay_timer = None

        self.create_ui()
    
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
        t = np.linspace(0, duration, int(self.sample_rate * duration), False)
        tone = np.sin(2 * np.pi * self.frequency * t)
        return tone * 0.3  # Reduce volume
    
    def play_morse(self, morse_sequence):
        signals = []
        gap = np.zeros(int(self.dot_duration * self.sample_rate * 0.5))
        
        for symbol in morse_sequence:
            if symbol == '.':
                signals.append(self.generate_tone(self.dot_duration))
            elif symbol == '-':
                signals.append(self.generate_tone(self.dash_duration))
            signals.append(gap)
        
        signal = np.concatenate(signals)
        sd.play(signal, self.sample_rate, blocking=True)

    def play_bell(self, duration: float = 0.35, frequency: int = 1000):
        """Play a short bell sound (decaying sine) to mark session end."""
        t = np.linspace(0, duration, int(self.sample_rate * duration), False)
        # exponential decay envelope
        envelope = np.exp(-6 * t)
        tone = np.sin(2 * np.pi * frequency * t) * envelope
        sd.play(tone * 0.45, self.sample_rate, blocking=True)
    
    def play_morse_and_reset_timer(self, morse_sequence):
        if self._is_playing:
            return
        self._is_playing = True
        self.play_time = datetime.now()
        self.play_morse(morse_sequence)
        self._is_playing = False
        # schedule a one-shot replay after 1.5s if no answer
        try:
            # cancel previous scheduled replay if any
            if self._replay_timer is not None:
                try:
                    self._replay_timer.stop()
                except Exception:
                    pass
                self._replay_timer = None
        finally:
            # schedule new one-shot timer
            self._replay_timer = ui.timer(1.5, self._replay_if_still_waiting, once=True)

    def _cancel_replay_timer(self):
        if self._replay_timer is not None:
            try:
                self._replay_timer.stop()
            except Exception:
                pass
            self._replay_timer = None

    def _replay_if_still_waiting(self):
        # Called by the one-shot timer after 1.5s; play again only if session still waiting for input
        if not self.session_active or self.current_char is None or self.play_time is None:
            return
        # ensure enough time has actually elapsed
        elapsed = (datetime.now() - self.play_time).total_seconds()
        if elapsed >= 1.5 and not self._is_playing:
            morse = MORSE_CODE[self.current_char]
            self.play_morse_and_reset_timer(morse)
    
    def check_replay_timer(self):
        if not self.session_active or self._is_playing or not self.current_char or not self.play_time:
            return
        elapsed = (datetime.now() - self.play_time).total_seconds()
        if elapsed >= 1.5:
            morse = MORSE_CODE[self.current_char]
            self.play_morse_and_reset_timer(morse)
    
    def start_replay_checker(self):
        ui.timer(0.1, self.check_replay_timer)
    
    def next_char(self):
        self.current_char = random.choice(list(MORSE_CODE.keys()))
        morse = MORSE_CODE[self.current_char]
        self.play_morse_and_reset_timer(morse)
        self.status_label.text = 'Listening...'
        self.status_label.classes('text-blue-600')
        self.update_ui()
    
    def handle_keypress(self, e):
        if not self.session_active or self.current_char is None or self.play_time is None:
            return
        if len(str(e.key)) == 1:
            pressed_char = str(e.key).upper()
            reaction_time = (datetime.now() - self.play_time).total_seconds()
            if pressed_char == self.current_char:
                self.scores.append((self.current_char, reaction_time, True, pressed_char))
                self.best_score = min(self.best_score, reaction_time)
                # update average history
                correct_times = [t for (_, t, c, _) in self.scores if c]
                avg = sum(correct_times) / len(correct_times) if correct_times else 0.0
                self.avg_history.append(avg)
                self.status_label.text = f'Correct! ({self.current_char})'
                self.status_label.classes('text-green-600')
            else:
                self.scores.append((self.current_char, reaction_time, False, pressed_char))
                self.status_label.text = f'Incorrect: you pressed {pressed_char}, expected {self.current_char}'
                self.status_label.classes('text-red-600')
            # clear current prompt and cancel any scheduled replay
            self.current_char = None
            self.play_time = None
            self._cancel_replay_timer()
            self.update_ui()
            # Move to next character if session is still active
            if self.session_active:
                ui.timer(0.5, self.next_char, once=True)
    def start_session(self):
        if self.session_active:
            return
        self.session_length = int(self.length_input.value)
        self.session_active = True
        self.scores.clear()
        self.session_start_time = time.time()
        self.status_label.text = 'Session started!'
        self.status_label.classes('text-blue-600')
        self.update_ui()
        # buffer 1 second before first character
        ui.timer(1.0, self.next_char, once=True)
        # End session after specified time
        ui.timer(self.session_length, self.stop_session, once=True)

    def stop_session(self):
        if not self.session_active:
            return
        self.session_active = False
        self.current_char = None
        self.play_time = None
        # Play a bell to indicate session end
        try:
            self.play_bell()
        except Exception:
            # if audio fails, just continue
            pass
        self.status_label.text = 'Session stopped.'
        self.status_label.classes('text-gray-600')
        self.update_ui()


# Create game instance and set up keyboard handler
game = MorseGame()
ui.keyboard(on_key=game.handle_keypress)

# Start the app
ui.run(title='Morse Code Echo Game')