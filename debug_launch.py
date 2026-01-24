import sys
import traceback
import os
import time

# Set log file path
log_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "crash_debug.log")

def log(msg):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    formatted_msg = f"[{timestamp}] {msg}"
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(formatted_msg + "\n")
    except Exception as e:
        print(f"Failed to write to log: {e}")
    print(formatted_msg)

log("========== Debug Session Started ==========")
log(f"Python executable: {sys.executable}")
log(f"Working directory: {os.getcwd()}")

try:
    log("Importing main module...")
    import main
    log("Main module imported successfully.")
    
    if hasattr(main, 'VoiceChatApp'):
        log("Found VoiceChatApp class. Initializing...")
        try:
            app = main.VoiceChatApp()
            log("VoiceChatApp constructor returned (likely mainloop exited).")
        except Exception as e:
            log(f"Exception during VoiceChatApp execution: {e}")
            raise # Re-raise to be caught by outer block
    else:
        log("ERROR: VoiceChatApp class not found in main module.")

except Exception:
    log("CRITICAL CRASH DETECTED:")
    log(traceback.format_exc())
    print("CRASH_DETECTED_MARKER")
    
log("========== Debug Session Ended ==========")
