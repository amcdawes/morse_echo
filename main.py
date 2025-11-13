import multiprocessing
multiprocessing.set_start_method("spawn", force=True)

from nicegui import ui
import numpy as np
import sounddevice as sd
import random
import time
from datetime import datetime
import argparse
import sqlite3
import os
from pathlib import Path

# Morse code mapping
MORSE_CODE = {
    'A': '.-', 'B': '-...', 'C': '-.-.', 'D': '-..', 'E': '.',
    'F': '..-.', 'G': '--.', 'H': '....', 'I': '..', 'J': '.---',
    'K': '-.-', 'L': '.-..', 'M': '--', 'N': '-.', 'O': '---',
    'P': '.--.', 'Q': '--.-', 'R': '.-.', 'S': '...', 'T': '-',
    'U': '..-', 'V': '...-', 'W': '.--', 'X': '-..-', 'Y': '-.--',
    'Z': '--..'
}

class SessionHistory:
    """Manages persistent storage of training sessions using SQLite."""
    
    def __init__(self, db_path=None):
        if db_path is None:
            # Store in user's home directory
            home = Path.home()
            db_path = home / '.morse_echo.db'
        
        self.db_path = str(db_path)
        self._init_schema()
    
    def _get_connection(self):
        """Get a fresh database connection for each operation."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # Enable column access by name
        return conn
    
    def _init_schema(self):
        """Create tables if they don't exist."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Sessions table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                duration INTEGER NOT NULL,
                wpm INTEGER NOT NULL,
                total_attempts INTEGER NOT NULL,
                correct_attempts INTEGER NOT NULL,
                accuracy REAL NOT NULL,
                avg_response_time REAL
            )
        ''')
        
        # Individual attempts table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                char TEXT NOT NULL,
                response_time REAL NOT NULL,
                correct BOOLEAN NOT NULL,
                pressed_char TEXT,
                FOREIGN KEY(session_id) REFERENCES sessions(id)
            )
        ''')
        
        # Character statistics table (aggregated)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS character_stats (
                char TEXT PRIMARY KEY,
                total_attempts INTEGER NOT NULL DEFAULT 0,
                correct_attempts INTEGER NOT NULL DEFAULT 0,
                total_response_time REAL NOT NULL DEFAULT 0.0,
                last_practiced TEXT
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def save_session(self, scores, wpm, duration):
        """Save a completed session to the database."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Calculate session statistics
        total_attempts = len(scores)
        correct_attempts = sum(1 for (_, _, correct, _) in scores if correct)
        accuracy = (correct_attempts / total_attempts * 100) if total_attempts > 0 else 0.0
        
        correct_times = [t for (_, t, correct, _) in scores if correct]
        avg_response_time = (sum(correct_times) / len(correct_times)) if correct_times else None
        
        timestamp = datetime.now().isoformat()
        
        # Insert session record
        cursor.execute('''
            INSERT INTO sessions (timestamp, duration, wpm, total_attempts, correct_attempts, accuracy, avg_response_time)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (timestamp, duration, wpm, total_attempts, correct_attempts, accuracy, avg_response_time))
        
        session_id = cursor.lastrowid
        
        # Insert individual attempts
        for char, response_time, correct, pressed_char in scores:
            cursor.execute('''
                INSERT INTO attempts (session_id, char, response_time, correct, pressed_char)
                VALUES (?, ?, ?, ?, ?)
            ''', (session_id, char, response_time, correct, pressed_char))
            
            # Update character statistics
            cursor.execute('''
                INSERT INTO character_stats (char, total_attempts, correct_attempts, total_response_time, last_practiced)
                VALUES (?, 1, ?, ?, ?)
                ON CONFLICT(char) DO UPDATE SET
                    total_attempts = total_attempts + 1,
                    correct_attempts = correct_attempts + ?,
                    total_response_time = total_response_time + ?,
                    last_practiced = ?
            ''', (char, 1 if correct else 0, response_time, timestamp,
                  1 if correct else 0, response_time, timestamp))
        
        conn.commit()
        conn.close()
        return session_id
    
    def get_recent_sessions(self, limit=20):
        """Get the most recent sessions."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM sessions
            ORDER BY timestamp DESC
            LIMIT ?
        ''', (limit,))
        result = cursor.fetchall()
        conn.close()
        return result
    
    def get_character_stats(self):
        """Get aggregated statistics for each character."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT 
                char,
                total_attempts,
                correct_attempts,
                CAST(correct_attempts AS REAL) / total_attempts * 100 as accuracy,
                total_response_time / NULLIF(correct_attempts, 0) as avg_response_time,
                last_practiced
            FROM character_stats
            WHERE total_attempts > 0
            ORDER BY char
        ''')
        result = cursor.fetchall()
        conn.close()
        return result
    
    def get_progress_data(self, days=30):
        """Get session data for progress charts."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cutoff = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        from datetime import timedelta
        cutoff = (cutoff - timedelta(days=days)).isoformat()
        
        cursor.execute('''
            SELECT timestamp, accuracy, avg_response_time, wpm, total_attempts
            FROM sessions
            WHERE timestamp >= ?
            ORDER BY timestamp ASC
        ''', (cutoff,))
        result = cursor.fetchall()
        conn.close()
        return result
    
    def clear_all_data(self):
        """Delete all session data from the database."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute('DELETE FROM attempts')
        cursor.execute('DELETE FROM sessions')
        cursor.execute('DELETE FROM character_stats')
        
        conn.commit()
        conn.close()
    
    def export_to_csv(self, filename):
        """Export all session data to CSV file."""
        import csv
        
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Get all attempts with session info
        cursor.execute('''
            SELECT 
                s.timestamp,
                s.wpm,
                a.char,
                a.response_time,
                a.correct,
                a.pressed_char
            FROM attempts a
            JOIN sessions s ON a.session_id = s.id
            ORDER BY s.timestamp, a.id
        ''')
        
        rows = cursor.fetchall()
        conn.close()
        
        # Write to CSV
        with open(filename, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['Timestamp', 'WPM', 'Character', 'ResponseTime', 'Correct', 'PressedChar'])
            for row in rows:
                writer.writerow([
                    row['timestamp'],
                    row['wpm'],
                    row['char'],
                    row['response_time'],
                    'Yes' if row['correct'] else 'No',
                    row['pressed_char'] if row['pressed_char'] else ''
                ])

class MorseGame:
    def __init__(self, debug=False):
        # Debug mode
        self.debug = debug
        
        # Initialize session history
        self.history = SessionHistory()
        
        # Audio parameters
        self.sample_rate = 44100
        self.wpm = 20  # Default words per minute
        self.dot_duration = self._calculate_dot_duration(self.wpm)
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

        # UI elements
        self.score_list = None
        self.status_label = None
        self.summary_label = None
        self.start_button = None
        self.stop_button = None
        self.length_input = None
        self.wpm_slider = None
        self.response_times = []  # list of response times for correct answers
        self.score_display = None

        self.create_ui()
    
    def log(self, message):
        """Print debug message if debug mode is enabled."""
        if self.debug:
            timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]
            print(f"[{timestamp}] {message}")
    
    def _calculate_dot_duration(self, wpm):
        """Calculate dot duration from WPM. Standard word = 50 dot durations (PARIS method)."""
        return 1.2 / wpm
    
    def update_wpm(self, new_wpm):
        """Update WPM and recalculate timing."""
        self.wpm = new_wpm
        self.dot_duration = self._calculate_dot_duration(new_wpm)
        self.dash_duration = self.dot_duration * 3
        self.log(f"update_wpm: WPM set to {new_wpm}, dot_duration={self.dot_duration:.3f}s")
    
    def create_ui(self):
        with ui.column().classes('w-full items-center justify-center p-4'):
            # Score display in top right
            with ui.row().classes('w-full justify-end mb-2'):
                self.score_display = ui.label('').classes('text-xl font-bold')
            
            # Tabs for Practice and History
            with ui.tabs().classes('w-full') as tabs:
                practice_tab = ui.tab('Practice')
                history_tab = ui.tab('History')
            
            with ui.tab_panels(tabs, value=practice_tab).classes('w-full'):
                # Practice Tab
                with ui.tab_panel(practice_tab):
                    self.create_practice_ui()
                
                # History Tab
                with ui.tab_panel(history_tab):
                    self.create_history_ui()
    
    def create_practice_ui(self):
        """Create the practice session UI."""
        # Two-column layout
        with ui.row().classes('gap-6 w-full items-start justify-center'):
            # Left column: Controls and Graph
            with ui.column().classes('flex-none').style('width: 400px'):
                # Controls section
                with ui.card().classes('w-full p-3'):
                    ui.label('Session Controls').classes('text-lg font-bold mb-1')
                    self.length_input = ui.number(label='Session Length (seconds)', value=60, min=10, max=600).classes('w-full')
                    self.wpm_slider = ui.slider(min=10, max=30, step=2, value=20).props('label-always').classes('w-full mt-1')
                    ui.label().bind_text_from(self.wpm_slider, 'value', backward=lambda v: f'Speed: {v} WPM')
                    self.wpm_slider.on_value_change(lambda e: self.update_wpm(e.value))
                    with ui.row().classes('gap-2 mt-2 w-full'):
                        self.start_button = ui.button('Start', on_click=self.start_session).classes('flex-1')
                        self.stop_button = ui.button('Stop', on_click=self.stop_session).classes('flex-1')
                    self.status_label = ui.label('Press Start to begin').classes('text-gray-600 mt-1')
                    self.summary_label = ui.label('').classes('mt-2 text-sm')
                
                # Graph section
                with ui.card().classes('w-full p-3 mt-3'):
                    ui.label('Response Time').classes('text-lg font-bold mb-1')
                    self.chart = ui.html('', sanitize=False).classes('w-full')
            
            # Right column: Results history (taller, fixed width)
            with ui.column().classes('flex-none').style('width: 400px'):
                with ui.card().classes('w-full p-3 h-full'):
                    ui.label('Current Session').classes('text-lg font-bold mb-1')
                    self.score_list = ui.html('', sanitize=False).classes('overflow-y-auto font-mono pr-2').style('height: 600px')
    
    def create_history_ui(self):
        """Create the history and analytics UI."""
        with ui.column().classes('w-full items-center gap-4'):
            # Recent Sessions
            with ui.card().classes('w-full max-w-4xl p-4'):
                with ui.row().classes('w-full justify-between items-center mb-2'):
                    ui.label('Recent Sessions').classes('text-xl font-bold')
                    with ui.row().classes('gap-2'):
                        ui.button('Refresh', icon='refresh', on_click=self.refresh_history).props('flat')
                        ui.button('Export CSV', icon='download', on_click=self.export_csv).props('flat')
                        ui.button('Clear History', icon='delete', on_click=self.confirm_clear_history).props('flat color=red')
                
                self.sessions_table = ui.table(
                    columns=[
                        {'name': 'date', 'label': 'Date', 'field': 'date', 'align': 'left'},
                        {'name': 'time', 'label': 'Time', 'field': 'time', 'align': 'left'},
                        {'name': 'wpm', 'label': 'WPM', 'field': 'wpm', 'align': 'center'},
                        {'name': 'attempts', 'label': 'Attempts', 'field': 'attempts', 'align': 'center'},
                        {'name': 'correct', 'label': 'Correct', 'field': 'correct', 'align': 'center'},
                        {'name': 'accuracy', 'label': 'Accuracy', 'field': 'accuracy', 'align': 'center'},
                        {'name': 'avg_time', 'label': 'Avg Time', 'field': 'avg_time', 'align': 'center'},
                    ],
                    rows=[],
                    row_key='id'
                ).classes('w-full')
            
            # Character Statistics
            with ui.card().classes('w-full max-w-4xl p-4'):
                ui.label('Character Statistics').classes('text-xl font-bold mb-2')
                self.char_stats_grid = ui.html('', sanitize=False).classes('w-full')
            
            # Load initial data
            self.refresh_history()
    
    def refresh_history(self):
        """Refresh history data from database."""
        # Get recent sessions
        sessions = self.history.get_recent_sessions(20)
        rows = []
        for session in sessions:
            dt = datetime.fromisoformat(session['timestamp'])
            rows.append({
                'id': session['id'],
                'date': dt.strftime('%Y-%m-%d'),
                'time': dt.strftime('%H:%M'),
                'wpm': session['wpm'],
                'attempts': session['total_attempts'],
                'correct': session['correct_attempts'],
                'accuracy': f"{session['accuracy']:.1f}%",
                'avg_time': f"{session['avg_response_time']:.2f}s" if session['avg_response_time'] else "N/A"
            })
        
        self.sessions_table.rows = rows
        
        # Get character statistics
        char_stats = self.history.get_character_stats()
        
        # Create a grid display for character stats
        char_html = '<div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(80px, 1fr)); gap: 8px;">'
        for stat in char_stats:
            char = stat['char']
            accuracy = stat['accuracy']
            avg_time = stat['avg_response_time']
            attempts = stat['total_attempts']
            
            # Color code by accuracy
            if accuracy >= 90:
                color = '#22c55e'  # green
            elif accuracy >= 75:
                color = '#eab308'  # yellow
            else:
                color = '#ef4444'  # red
            
            # Handle null avg_time
            avg_time_str = f"{avg_time:.2f}s" if avg_time is not None else "N/A"
            
            char_html += f'''
                <div style="border: 2px solid {color}; border-radius: 8px; padding: 8px; text-align: center;">
                    <div style="font-size: 24px; font-weight: bold;">{char}</div>
                    <div style="font-size: 12px; color: #666;">{accuracy:.0f}%</div>
                    <div style="font-size: 11px; color: #888;">{avg_time_str}</div>
                    <div style="font-size: 10px; color: #999;">n={attempts}</div>
                </div>
            '''
        
        char_html += '</div>'
        self.char_stats_grid.content = char_html
    
    def export_csv(self):
        """Export session history to CSV file."""
        from datetime import datetime as dt
        timestamp = dt.now().strftime('%Y%m%d_%H%M%S')
        filename = f'morse_echo_export_{timestamp}.csv'
        
        try:
            self.history.export_to_csv(filename)
            ui.notify(f'Exported to {filename}', type='positive', position='top')
            self.log(f"export_csv: Successfully exported to {filename}")
        except Exception as e:
            ui.notify(f'Export failed: {e}', type='negative', position='top')
            self.log(f"export_csv: Failed - {e}")
    
    def confirm_clear_history(self):
        """Show confirmation dialog before clearing history."""
        with ui.dialog() as dialog, ui.card():
            ui.label('Clear All History?').classes('text-lg font-bold')
            ui.label('This will permanently delete all session data and cannot be undone.').classes('text-gray-600 mb-4')
            with ui.row().classes('gap-2'):
                ui.button('Cancel', on_click=dialog.close).props('outline')
                ui.button('Clear All Data', on_click=lambda: self.clear_history(dialog)).props('color=red')
        dialog.open()
    
    def clear_history(self, dialog):
        """Clear all history data from database."""
        try:
            self.history.clear_all_data()
            dialog.close()
            self.refresh_history()
            ui.notify('All history cleared', type='positive', position='top')
            self.log("clear_history: Successfully cleared all data")
        except Exception as e:
            ui.notify(f'Failed to clear history: {e}', type='negative', position='top')
            self.log(f"clear_history: Failed - {e}")
    
    def update_ui(self):
        # Update score history (show more items now that we have taller display)
        def render_row(item):
            played_char, t, correct, pressed = item
            if correct:
                return f'<span style="color: green">{"{:.2f}s".format(t)} - {played_char}</span>'
            else:
                return f'<span style="color: red">ERROR - played: {played_char}, pressed: {pressed}</span>'

        scores_html = '<br>'.join(
            render_row(item) for item in reversed(self.scores)
        )
        self.score_list.content = f'<div style="white-space: pre-wrap">{scores_html if scores_html else "No results yet"}</div>'
        if not self.session_active:
            self.status_label.text = 'Press Start to begin'
            self.status_label.classes('text-gray-600')

        # Update response time chart (simple SVG sparkline)
        if self.response_times:
            width = 360
            height = 120
            padding = 8
            vals = self.response_times[-40:]  # Show last 40 responses
            max_v = max(vals) if vals else 1.0
            min_v = min(vals) if vals else 0.0
            span = max_v - min_v if max_v > min_v else 1.0
            pts = []
            for i, v in enumerate(vals):
                x = padding + i * (width - 2 * padding) / max(1, len(vals) - 1) if len(vals) > 1 else padding + (width - 2 * padding) / 2
                y = padding + (height - 2 * padding) * (1 - (v - min_v) / span) if span > 0 else padding + (height - 2 * padding) / 2
                pts.append(f"{x:.1f},{y:.1f}")
            poly = ' '.join(pts)
            svg = f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">'
            svg += f'<rect x="0" y="0" width="{width}" height="{height}" fill="#fff" stroke="#eee"/>'
            svg += f'<polyline fill="none" stroke="#2b8bdb" stroke-width="2" points="{poly}" />'
            # draw last value
            last = vals[-1]
            last_x = padding + (len(vals) - 1) * (width - 2 * padding) / max(1, len(vals) - 1) if len(vals) > 1 else padding + (width - 2 * padding) / 2
            last_y = padding + (height - 2 * padding) * (1 - (last - min_v) / span) if span > 0 else padding + (height - 2 * padding) / 2
            svg += f'<circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="3" fill="#2b8bdb" />'
            svg += '</svg>'
            self.chart.content = svg
        else:
            self.chart.content = '<div style="color: #666; padding: 20px">No data yet</div>'
    
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
        self.log(f"play_morse_and_reset_timer: Playing morse for '{self.current_char}', play_time={self.play_time}")
        self.play_morse(morse_sequence)
        self._is_playing = False

    
    
    
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
                # Add response time to chart data
                self.response_times.append(reaction_time)
                self.status_label.text = f'Correct! ({self.current_char})'
                self.status_label.classes('text-green-600')
                self.log(f"handle_keypress: CORRECT answer")
            else:
                self.scores.append((self.current_char, reaction_time, False, pressed_char))
                self.status_label.text = f'Incorrect: you pressed {pressed_char}, expected {self.current_char}'
                self.status_label.classes('text-red-600')
                self.log(f"handle_keypress: INCORRECT answer")
            # clear current prompt
            self.log(f"handle_keypress: Clearing current_char and play_time")
            self.current_char = None
            self.play_time = None
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
        self.score_display.text = ''  # Clear score at start
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
        # Cancel session timer if running
        if hasattr(self, '_session_timer') and self._session_timer is not None:
            try:
                self._session_timer.stop()
            except Exception:
                pass
            self._session_timer = None
        # Calculate and display final score
        if self.scores:
            correct = sum(1 for (_, _, c, _) in self.scores if c)
            total = len(self.scores)
            percentage = (correct / total * 100) if total > 0 else 0
            # also compute average response time for correct answers
            correct_times = [t for (_, t, c, _) in self.scores if c]
            avg = (sum(correct_times) / len(correct_times)) if correct_times else None
            avg_text = f"{avg:.2f}s" if avg is not None else "N/A"
            self.score_display.text = f'Final Score: {correct}/{total} ({percentage:.1f}%), Avg: {avg_text}'
            
            # Save session to database
            try:
                self.history.save_session(self.scores, self.wpm, self.session_length)
                self.log(f"stop_session: Saved session with {total} attempts")
                # Refresh history display to show new session
                self.refresh_history()
            except Exception as e:
                self.log(f"stop_session: Failed to save session: {e}")
        
        # Play a bell to indicate session end
        try:
            self.play_bell()
        except Exception:
            # if audio fails, just continue
            pass
        self.update_ui()


# Parse command-line arguments
parser = argparse.ArgumentParser(description='Morse Code Echo Game')
parser.add_argument('--debug', action='store_true', help='Enable debug logging to console')
parser.add_argument('--native', action='store_true', help='Run as native app using pywebview')
args = parser.parse_args()

# Create game instance and set up keyboard handler
game = MorseGame(debug=args.debug)
ui.keyboard(on_key=game.handle_keypress)

# Start the app
if args.native:
    ui.run(title='Morse Code Echo Game', native=True, window_size=(1100, 800))
else:
    ui.run(title='Morse Code Echo Game')