"""
Toast Notification System for StreamNook
A modern, animated toast notification system based on the "Frost Glass" theme.
(Version 4: Bottom-left, 50% opacity, single-line text)

Usage:
    from toast_notifications import ToastManager
    
    # In your MainWindow.__init__:
    self.toast_manager = ToastManager(self)
    
    # Show notifications:
    self.toast_manager.show_info("Information message")
    self.toast_manager.show_success("Success message!")
    self.toast_manager.show_warning("Warning message")
    self.toast_manager.show_error("Error message")
"""

from PySide6 import QtCore, QtGui, QtWidgets


class ToastNotification(QtWidgets.QFrame):
    """Modern animated toast notification widget (Frost Glass Theme)"""
    
    # Toast types with colors
    INFO = ("info", "#2196F3")
    SUCCESS = ("success", "#4CAF50")
    WARNING = ("warning", "#FF9800")
    ERROR = ("error", "#F44336")
    
    closed = QtCore.Signal()
    
    def __init__(self, message: str, toast_type: tuple = INFO, duration: int = 3000, parent=None):
        super().__init__(parent)
        self.message = message
        self.toast_type = toast_type[0]
        # self.color = toast_type[1] # Color is no longer used for icons
        self.duration = duration
        
        self.setWindowFlags(QtCore.Qt.FramelessWindowHint | QtCore.Qt.Tool)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating)
        
        self._setup_ui()
        self._setup_animations()
        
    def _setup_ui(self):
        """Setup the toast UI"""
        self.setFixedWidth(400)
        self.setMinimumHeight(70)
        self.setMaximumHeight(90)
        
        # Main layout
        main_layout = QtWidgets.QVBoxLayout(self)
        # Margins allow space for the drop shadow
        main_layout.setContentsMargins(10, 10, 10, 10)
        
        # Content container
        self.container = QtWidgets.QFrame()
        self.container.setObjectName("ToastContainer")
        self.container.setStyleSheet(f"""
            #ToastContainer {{
                background-color: rgba(151, 177, 185, 0.5); /* Glass Effect (50% opacity) */
                border: 1px solid rgba(151, 177, 185, 0.1); /* Section Border */
                border-radius: 8px; /* From .section */
                padding: 0px;
                font-family: 'Satoshi', sans-serif;
            }}
        """)
        
        # Use a QVBoxLayout to center the text vertically
        container_layout = QtWidgets.QVBoxLayout(self.container)
        container_layout.setContentsMargins(18, 14, 18, 14)
        container_layout.setSpacing(0)
        
        # Message
        message_label = QtWidgets.QLabel(self.message)
        message_label.setWordWrap(False) # MODIFIED: Ensure text stays on one line
        message_label.setAlignment(QtCore.Qt.AlignCenter) # Center text
        message_label.setStyleSheet("""
            color: white; /* Primary Text */
            font-size: 15px; /* Larger text */
            background: transparent;
            font-weight: 700; /* Bold weight */
            font-family: 'Satoshi', sans-serif;
        """)
        container_layout.addWidget(message_label, 1, QtCore.Qt.AlignCenter)
        
        main_layout.addWidget(self.container)
        
        # Drop shadow effect (subtle, per spec)
        shadow = QtWidgets.QGraphicsDropShadowEffect()
        shadow.setBlurRadius(20)
        shadow.setColor(QtGui.QColor(0, 0, 0, 50)) # Subtle shadow (~20% opacity)
        shadow.setOffset(0, 4)
        self.container.setGraphicsEffect(shadow)
        
    def _setup_animations(self):
        """Setup slide and fade animations"""
        # Slide in animation
        self.slide_in = QtCore.QPropertyAnimation(self, b"pos")
        self.slide_in.setDuration(350)
        self.slide_in.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        
        # Slide out animation
        self.slide_out = QtCore.QPropertyAnimation(self, b"pos")
        self.slide_out.setDuration(250)
        self.slide_out.setEasingCurve(QtCore.QEasingCurve.InCubic)
        self.slide_out.finished.connect(self._on_slide_out_finished)
        
        # Opacity animation
        self.opacity_effect = QtWidgets.QGraphicsOpacityEffect()
        self.setGraphicsEffect(self.opacity_effect)
        
        self.fade_in = QtCore.QPropertyAnimation(self.opacity_effect, b"opacity")
        self.fade_in.setDuration(350)
        self.fade_in.setStartValue(0.0)
        self.fade_in.setEndValue(1.0)
        self.fade_in.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        
        self.fade_out = QtCore.QPropertyAnimation(self.opacity_effect, b"opacity")
        self.fade_out.setDuration(250)
        self.fade_out.setStartValue(1.0)
        self.fade_out.setEndValue(0.0)
        self.fade_out.setEasingCurve(QtCore.QEasingCurve.InCubic)
        
    def show_toast(self, parent_widget):
        """Show the toast with animation - slides from bottom-left"""
        if not parent_widget:
            return
        
        # Get parent window geometry
        parent_geometry = parent_widget.geometry()
        parent_height = parent_geometry.height()
        
        # Calculate position relative to parent window
        margin = 20
        
        # Start position (off-screen to the left)
        start_x = -self.width() - 20
        
        # End position (visible at bottom-left)
        end_x = margin
        y_pos = parent_height - self.height() - margin
        
        # Convert to global coordinates
        global_start = parent_widget.mapToGlobal(QtCore.QPoint(start_x, y_pos))
        global_end = parent_widget.mapToGlobal(QtCore.QPoint(end_x, y_pos))
        
        # Set start position
        self.move(global_start)
        
        # Configure slide animation
        self.slide_in.setStartValue(self.pos())
        self.slide_in.setEndValue(global_end)
        
        # Show and animate
        self.show()
        self.raise_()
        self.slide_in.start()
        self.fade_in.start()
        
        # Auto-hide timer
        if self.duration > 0:
            QtCore.QTimer.singleShot(self.duration, self.hide_toast)
    
    def hide_toast(self):
        """Hide the toast with animation - slides left"""
        if not self.isVisible():
            return
        
        # Slide left off screen
        current_pos = self.pos()
        end_x = -self.width() - 20
        
        self.slide_out.setStartValue(current_pos)
        self.slide_out.setEndValue(QtCore.QPoint(end_x, current_pos.y()))
        
        self.slide_out.start()
        self.fade_out.start()
    
    def _on_slide_out_finished(self):
        """Cleanup after slide out"""
        self.hide()
        self.closed.emit()
        self.deleteLater()


class LoadingToast(QtWidgets.QFrame):
    """Special toast for loading streams (Frost Glass Theme, with emote support)"""
    
    closed = QtCore.Signal()
    
    def __init__(self, message: str, parent=None):
        super().__init__(parent)
        self.message = message
        
        # --- ADDED FOR EMOTES ---
        self.movie = QtGui.QMovie(self)
        self._buffer = None
        # --- END ---
        
        self.setWindowFlags(QtCore.Qt.FramelessWindowHint | QtCore.Qt.Tool)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating)
        
        self._setup_ui()
        self._setup_animations()
        
    def _setup_ui(self):
        """Setup the loading toast UI"""
        self.setFixedWidth(420)
        self.setMinimumHeight(70)
        self.setMaximumHeight(90)
        
        # Main layout
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        
        # Content container
        self.container = QtWidgets.QFrame()
        self.container.setObjectName("LoadingToastContainer")
        
        # Use Twitch purple as substituted accent color per "Frost Glass" spec
        accent_rgb = "145, 71, 255" # #9147ff
        
        self.container.setStyleSheet(f"""
            #LoadingToastContainer {{
                background-color: rgba({accent_rgb}, 0.5); /* Glass Effect (50% opacity) */
                border: 1px solid rgba({accent_rgb}, 0.1); /* Section Border */
                border-radius: 8px; /* From .section */
                padding: 0px;
                font-family: 'Satoshi', sans-serif;
            }}
        """)
        
        # --- MODIFIED: Use a QHBoxLayout to hold emote + text ---
        container_layout = QtWidgets.QHBoxLayout(self.container)
        container_layout.setContentsMargins(18, 14, 18, 14)
        container_layout.setSpacing(12) # Space between emote and text
        container_layout.setAlignment(QtCore.Qt.AlignCenter) # Center content
        
        # Emote Label (NEW)
        self.emote_label = QtWidgets.QLabel()
        self.emote_label.setFixedSize(40, 40)
        self.emote_label.setAlignment(QtCore.Qt.AlignCenter)
        self.emote_label.setScaledContents(True)
        self.emote_label.setMovie(self.movie)
        self.emote_label.setVisible(False) # Hide until emote is set
        container_layout.addWidget(self.emote_label)
        
        # Message
        self.message_label = QtWidgets.QLabel(self.message)
        self.message_label.setWordWrap(False) # MODIFIED: Ensure text stays on one line
        self.message_label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter) # Align next to emote
        self.message_label.setStyleSheet("""
            color: white; /* Primary Text */
            font-size: 15px; /* Larger text */
            background: transparent;
            font-weight: 700; /* Bold weight */
            font-family: 'Satoshi', sans-serif;
        """)
        container_layout.addWidget(self.message_label, 1) # Give message label stretch factor
        
        main_layout.addWidget(self.container)
        
        # Drop shadow effect (subtle, per spec)
        shadow = QtWidgets.QGraphicsDropShadowEffect()
        shadow.setBlurRadius(20)
        shadow.setColor(QtGui.QColor(0, 0, 0, 50)) # Subtle shadow (~20% opacity)
        shadow.setOffset(0, 4)
        self.container.setGraphicsEffect(shadow)
        
    def _setup_animations(self):
        """Setup slide and fade animations"""
        # Slide in animation
        self.slide_in = QtCore.QPropertyAnimation(self, b"pos")
        self.slide_in.setDuration(350)
        self.slide_in.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        
        # Slide out animation
        self.slide_out = QtCore.QPropertyAnimation(self, b"pos")
        self.slide_out.setDuration(250)
        self.slide_out.setEasingCurve(QtCore.QEasingCurve.InCubic)
        self.slide_out.finished.connect(self._on_slide_out_finished)
        
        # Opacity animation
        self.opacity_effect = QtWidgets.QGraphicsOpacityEffect()
        self.setGraphicsEffect(self.opacity_effect)
        
        self.fade_in = QtCore.QPropertyAnimation(self.opacity_effect, b"opacity")
        self.fade_in.setDuration(350)
        self.fade_in.setStartValue(0.0)
        self.fade_in.setEndValue(1.0)
        self.fade_in.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        
        self.fade_out = QtCore.QPropertyAnimation(self.opacity_effect, b"opacity")
        self.fade_out.setDuration(250)
        self.fade_out.setStartValue(1.0)
        self.fade_out.setEndValue(0.0)
        self.fade_out.setEasingCurve(QtCore.QEasingCurve.InCubic)
    
    @QtCore.Slot(str)
    def update_message(self, message: str):
        """Update the loading message (Thread-safe)"""
        # Check if we are in the wrong thread
        if QtCore.QThread.currentThread() != self.thread():
            # If so, post this method call to the correct thread's event loop
            QtCore.QMetaObject.invokeMethod(
                self, 
                "update_message", 
                QtCore.Qt.QueuedConnection, 
                QtCore.Q_ARG(str, message)
            )
            return
            
        self.message_label.setText(message)
    
    # --- NEW METHOD ---
    @QtCore.Slot(bytes)
    def set_emote_data(self, data: bytes):
        """Sets the animated emote from raw byte data."""
        if not data:
            self.emote_label.setVisible(False)
            return

        try:
            self.movie.stop()

            # Clean up old buffer
            if self._buffer:
                self._buffer.close()

            self._buffer = QtCore.QBuffer(self)
            self._buffer.setData(data)
            self._buffer.open(QtCore.QIODevice.ReadOnly)
            self.movie.setDevice(self._buffer)
            self.movie.start()
            
            # Show the label
            self.emote_label.setVisible(True)

            # Fallback to static if animation doesn't start
            QtCore.QTimer.singleShot(150, lambda: (
                None if self.movie.state() == QtGui.QMovie.Running else self._set_static_pixmap(data)
            ))
        except Exception as e:
            print(f"Error setting toast emote: {e}")
            self.emote_label.setVisible(False)
            
    # --- NEW HELPER METHOD ---
    def _set_static_pixmap(self, data: bytes, size=40):
        """Set static pixmap if animation fails"""
        pix = QtGui.QPixmap()
        if pix.loadFromData(data):
            self.emote_label.setPixmap(
                pix.scaled(size, size, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
            )
            self.emote_label.setVisible(True)
        else:
            self.emote_label.setVisible(False)
        
    def show_toast(self, parent_widget):
        """Show the toast with animation - slides from bottom-left"""
        if not parent_widget:
            return
        
        # Get parent window geometry
        parent_geometry = parent_widget.geometry()
        parent_height = parent_geometry.height()
        
        # Calculate position relative to parent window
        margin = 20
        
        # Start position (off-screen to the left)
        start_x = -self.width() - 20
        
        # End position (visible at bottom-left)
        end_x = margin
        y_pos = parent_height - self.height() - margin
        
        # Convert to global coordinates
        global_start = parent_widget.mapToGlobal(QtCore.QPoint(start_x, y_pos))
        global_end = parent_widget.mapToGlobal(QtCore.QPoint(end_x, y_pos))
        
        # Set start position
        self.move(global_start)
        
        # Configure slide animation
        self.slide_in.setStartValue(self.pos())
        self.slide_in.setEndValue(global_end)
        
        # Show and animate
        self.show()
        self.raise_()
        self.slide_in.start()
        self.fade_in.start()
    
    def hide_toast(self):
        """Hide the toast with animation - slides left"""
        if not self.isVisible():
            return
        
        # Slide left off screen
        current_pos = self.pos()
        end_x = -self.width() - 20
        
        self.slide_out.setStartValue(current_pos)
        self.slide_out.setEndValue(QtCore.QPoint(end_x, current_pos.y()))
        
        self.slide_out.start()
        self.fade_out.start()
    
    def _on_slide_out_finished(self):
        """Cleanup after slide out"""
        # --- ADDED CLEANUP ---
        self.movie.stop()
        if self._buffer:
            self._buffer.close()
            self._buffer = None
        # --- END ---
        
        self.hide()
        self.closed.emit()
        self.deleteLater()


class ToastManager(QtCore.QObject):
    """Manages multiple toast notifications with stacking"""
    
    def __init__(self, parent_widget):
        super().__init__(parent_widget)
        self.parent_widget = parent_widget
        self.active_toasts = []
        self.toast_spacing = 85  # Vertical spacing between toasts
        self.max_toasts = 5  # Maximum number of toasts to show at once
        self.loading_toast = None  # Special loading toast
        
    def show_info(self, message: str, duration: int = 3500):
        """Show info toast"""
        self._show_toast(message, ToastNotification.INFO, duration)
    
    def show_success(self, message: str, duration: int = 3000):
        """Show success toast"""
        self._show_toast(message, ToastNotification.SUCCESS, duration)
    
    def show_warning(self, message: str, duration: int = 4000):
        """Show warning toast"""
        self._show_toast(message, ToastNotification.WARNING, duration)
    
    def show_error(self, message: str, duration: int = 5000):
        """Show error toast"""
        self._show_toast(message, ToastNotification.ERROR, duration)
    
    def show_loading(self, message: str):
        """Show loading toast (no emote)"""
        # Hide any existing loading toast
        if self.loading_toast:
            self.loading_toast.hide_toast()
        
        self.loading_toast = LoadingToast(message, self.parent_widget)
        
        # Connect the closed signal to our slot
        self.loading_toast.closed.connect(self._on_loading_toast_closed)
        
        self.loading_toast.show_toast(self.parent_widget)
        return self.loading_toast
    
    def update_loading(self, message: str):
        """Update existing loading toast message"""
        if self.loading_toast:
            self.loading_toast.update_message(message)
    
    def hide_loading(self):
        """Hide the loading toast"""
        if self.loading_toast:
            self.loading_toast.hide_toast()
    
    @QtCore.Slot()
    def _on_loading_toast_closed(self):
        """
        Clear the reference to the loading toast when it closes.
        This prevents a RuntimeError from accessing a deleted C++ object.
        """
        if self.sender() == self.loading_toast:
            self.loading_toast = None
    
    def _show_toast(self, message: str, toast_type: tuple, duration: int):
        """Internal method to create and show toast"""
        # Remove oldest toast if we're at max capacity
        if len(self.active_toasts) >= self.max_toasts:
            oldest_toast = self.active_toasts[0]
            oldest_toast.hide_toast()
        
        toast = ToastNotification(message, toast_type, duration, self.parent_widget)
        toast.closed.connect(lambda: self._on_toast_closed(toast))
        
        # Add to active toasts
        self.active_toasts.append(toast)
        
        # Show the new toast
        toast.show_toast(self.parent_widget)
        
        # Reposition all toasts after a brief delay
        QtCore.QTimer.singleShot(50, self._reposition_toasts)
    
    def _reposition_toasts(self):
        """Reposition all active toasts with stacking (bottom-left of parent window)"""
        if not self.parent_widget:
            return
        
        parent_geometry = self.parent_widget.geometry()
        parent_height = parent_geometry.height()
        margin = 20
        
        # Filter out any toasts that might be in the process of closing
        visible_toasts = [t for t in self.active_toasts if t.isVisible()]
        
        for i, toast in enumerate(visible_toasts):
            x_pos = margin
            # Stack upwards: oldest (i=0) is at the bottom
            y_pos = parent_height - margin - toast.height() - (i * self.toast_spacing)
            
            target_pos = self.parent_widget.mapToGlobal(QtCore.QPoint(x_pos, y_pos))
            
            # Smoothly animate to new position if toast is already visible 
            if toast.pos() != target_pos:
                anim = QtCore.QPropertyAnimation(toast, b"pos")
                anim.setDuration(250)
                anim.setStartValue(toast.pos())
                anim.setEndValue(target_pos)
                anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)
                anim.start()
                # Keep reference to prevent garbage collection
                toast._reposition_anim = anim
    
    def _on_toast_closed(self, toast):
        """Handle toast closing"""
        if toast in self.active_toasts:
            self.active_toasts.remove(toast)
            # Reposition remaining toasts
            QtCore.QTimer.singleShot(100, self._reposition_toasts)
    
    def clear_all(self):
        """Clear all active toasts"""
        for toast in self.active_toasts[:]:
            toast.hide_toast()
        self.active_toasts.clear()