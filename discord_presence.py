#
# NEW discord_presence.py
# (Removes all 'is_connected' checks and relies on set_activity)
#
import time
import random # Import the random module
from PySide6 import QtCore
from concurrent.futures import ThreadPoolExecutor # Import ThreadPoolExecutor
try:
    import discordrpc
    from discordrpc import Activity, StatusDisplay, Button
    from discordrpc import DiscordNotOpened, RPCException
except ImportError:
    print("Discord-RPC library not found. Please install with: pip install discord-rpc")
    discordrpc = None
    DiscordNotOpened = Exception
    RPCException = Exception


class DiscordPresenceClient(QtCore.QObject):
    """
    A QObject worker for managing Discord Rich Presence in a separate thread.
    This version uses the 'discord-rpc' library by Senophyx.
    """
    # Thread pool for potentially blocking disconnect operation
    _disconnect_pool = ThreadPoolExecutor(max_workers=1)
    
    status_updated = QtCore.Signal(str)
    error_occurred = QtCore.Signal(str)
    disconnected = QtCore.Signal()
    

    def __init__(self, client_id: str):
        super().__init__()
        self.client_id = str(client_id) 
        self.presence = None
        self.connected = False
        self.start_time = int(time.time())

        # Define lists of funny Twitch-related phrases
        self.browsing_phrases = [
            "Channel Surfing for Poggers",
            "Lost in the Twitch Jungle",
            "Hunting for the next Hype Train",
            "Just Chatting... with myself",
            "AFK, but my eyes are still watching",
            "Searching for the legendary Kappa",
            "Dodging spoilers like a pro gamer",
            "Vibing in the VODs",
            "Exploring the emote-verse",
            "Where's the 'unfollow' button for reality?",
        ]

        self.idle_phrases = [
            "AFK (Away From Keyboard, but not from Twitch)",
            "Just chilling, waiting for the next stream",
            "Buffering... please wait",
            "In a staring contest with my screen",
            "My brain is in emote-only mode",
            "Currently respawning...",
            "Thinking about what to raid next",
            "Lost in thought, probably about subs",
            "Powered by caffeine and good vibes",
            "Waiting for the next 'clip that!' moment",
        ]

    def __del__(self):
        """Ensure the thread pool is shut down when the object is destroyed."""
        self._disconnect_pool.shutdown(wait=True)

    @QtCore.Slot()
    def connect_to_discord(self):
        """
        Connect to the local Discord client by setting an initial presence.
        This is the real test of the connection.
        """
        if self.connected:
            self.status_updated.emit("Already connected to Discord.")
            return
        
        if not discordrpc:
             self.error_occurred.emit("discord-rpc library is not imported.")
             return

        try:
            # 1. Create the RPC object. Connection is attempted here.
            self.presence = discordrpc.RPC(app_id=self.client_id)

            # 2. Immediately try to set the initial "Idle" activity.
            #    This will serve as our connection test.
            
            details = random.choice(self.browsing_phrases) # Random browsing phrase
            state = random.choice(self.idle_phrases)      # Random idle phrase
            large_image = "icon_256x256" # Asset key for the desktop icon
            
            kwargs = {
                "details": details,
                "state": state,
                "act_type": Activity.Playing,
                "large_image": large_image,
                "ts_start": self.start_time,
                "buttons": [
                    Button("Download Stream Nook", "https://github.com/winters27/StreamNook/")
                ],
            }
            
            # 3. Call set_activity. This is the test.
            self.presence.set_activity(**kwargs)

            # 4. If set_activity did not raise an exception, we are connected.
            self.connected = True
            self.status_updated.emit("Connected to Discord Rich Presence.")
            self.status_updated.emit(f"Presence updated: {details} · {state}")

        except DiscordNotOpened:
            self.connected = False
            self.error_occurred.emit("Discord client not found. Make sure the desktop app is running.")
        except RPCException as e:
            self.connected = False
            self.error_occurred.emit(f"Failed to set initial presence: {e}")
        except Exception as e:
            self.connected = False
            self.error_occurred.emit(f"Failed to connect to Discord: {e}")

    @QtCore.Slot(str, str, str, str, int, int, str, str)
    def update_presence(
        self,
        details: str,
        state: str,
        large_image: str,
        small_image: str,
        start_time: int,
        activity_type: int, # This will be mapped to act_type
        stream_url: str,
        category_name: str
    ):
        """
        Update Discord Rich Presence using RPC.set_activity()
        """
        if not self.connected or not self.presence:
            return
            
        if not discordrpc: return

        try:
            # --- Map activity_type int to the correct Enum ---
            if activity_type == 3:
                act_type_enum = Activity.Watching
            elif activity_type == 2:
                act_type_enum = Activity.Listening
            elif activity_type == 5:
                act_type_enum = Activity.Competing
            else:
                act_type_enum = Activity.Playing
            # --- End of mapping ---

            kwargs = {
                "details": details or None,
                "state": state or None,
                "act_type": act_type_enum,
                "large_image": large_image or None,
                "small_image": small_image or None,
                "buttons": [
                    Button("Download Stream Nook", "https://github.com/winters27/StreamNook/")
                ],
            }

            if isinstance(start_time, int) and start_time > 0:
                kwargs["ts_start"] = start_time

            if kwargs.get("large_image"):
                if category_name:
                    kwargs["large_text"] = category_name
                if stream_url:
                    kwargs["large_url"] = stream_url

            if kwargs.get("small_image"):
                kwargs["small_text"] = "Twitch"
                kwargs["small_url"] = "https://twitch.tv"

            send_kwargs = {k: v for k, v in kwargs.items() if v is not None}
            self.presence.set_activity(**send_kwargs)
            
            self.status_updated.emit(
                f"Presence updated: {send_kwargs.get('details','')} · {send_kwargs.get('state','')}"
            )
        except RPCException as e:
            self.error_occurred.emit(f"Failed to update presence: {e}")
        except Exception as e:
            self.error_occurred.emit(f"Unknown error updating presence: {e}")

    @QtCore.Slot()
    def clear_presence(self):
        """
        Clears the current Rich Presence (no-op if not connected).
        """
        if self.connected and self.presence:
            try:
                self.presence.clear()
                self.status_updated.emit("Presence cleared.")
            except Exception as e:
                self.error_occurred.emit(f"Failed to clear presence: {e}")


    @QtCore.Slot()
    def disconnect_from_discord(self):
        """
        Cleanly clear presence and disconnect from Discord RPC, then emit 'disconnected'.
        This operation is performed in a separate thread to prevent blocking.
        """
        # Clear status first for a tidy shutdown (this part is not blocking)
        if self.connected and self.presence:
            try:
                self.presence.clear()
                self.status_updated.emit("Presence cleared.")
            except Exception as e:
                self.error_occurred.emit(f"Failed to clear presence: {e}")

        # Now, submit the potentially blocking disconnect to the thread pool
        def _perform_disconnect_task():
            if self.connected and self.presence:
                try:
                    # Do NOT call self.presence.disconnect() as it calls sys.exit()
                    # Instead, just clear the presence and clean up the object.
                    self.presence.clear() # Ensure presence is cleared one last time
                    return "Discord RPC connection implicitly closed on app exit."
                except Exception as e:
                    # Catch any exception during clear and report it
                    return f"Error during presence clear on disconnect: {e}"
                finally:
                    # Ensure presence object is always cleaned up
                    if self.presence:
                        del self.presence
                        self.presence = None
            return "Not connected or no presence to disconnect."

        def _on_disconnect_finished(future):
            try:
                result_msg = future.result()
                self.status_updated.emit(result_msg)
            except Exception as e:
                self.error_occurred.emit(f"Unhandled error in disconnect task: {e}")
            finally:
                self.connected = False
                # Tell the GUI we are fully done; MainWindow listens and quits the thread.
                self.disconnected.emit()

        future = self._disconnect_pool.submit(_perform_disconnect_task)
        future.add_done_callback(_on_disconnect_finished)
