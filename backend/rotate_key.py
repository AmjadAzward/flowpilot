"""Rotate the key that encrypts saved credentials.

    python rotate_key.py              add a new key and re-encrypt every credential (old key kept as a fallback)
    python rotate_key.py --drop-old   also remove the old keys from the key file afterwards

Stop the API first, and back up flowpilot.db and flowpilot.key before you start. After --drop-old, any older copy of the
key file can no longer decrypt the credentials.
"""
import sys

from app import main


def run() -> None:
    drop = "--drop-old" in sys.argv
    print(f"Key file: {main.key_path()}")
    print("Stop the API and back up flowpilot.db and flowpilot.key first." + (" The old keys will be removed." if drop else ""))
    if input('Type "rotate" to continue: ').strip().lower() != "rotate":
        print("Cancelled.")
        return
    n = main.rotate_encryption_key(drop_old=drop)
    print(f"Done. Re-encrypted {n} credential{'s' if n != 1 else ''}." + (" Old keys removed." if drop else " The old key is still in the file; run again with --drop-old once you have checked everything works."))


if __name__ == "__main__":
    run()
