"""split_cr: tqdm \r redraws -> one line per update, no blank lines, \r\n not doubled across chunk boundaries."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from runner import split_cr

# Byte stream shape tqdm really writes (captured from a 2-step bar), split at awkward places.
chunks = [b"epoch 1\n", b"\r", b"  0%|          | 0/2 [00:00<?, ?it/s]", b"\r 50%|#####     | 1/2\r",
          b"\n" b"\r100%|##########| 2/2\r", b"\r100%|##########| 2/2\n", b"loss=0.5\r\n", b"\n", b"done\r\n"]
out, prev = b"", b"\n"
for c in chunks:
    o, prev = split_cr(c, prev)
    out += o
want = (b"epoch 1\n  0%|          | 0/2 [00:00<?, ?it/s]\n 50%|#####     | 1/2\n"
        b"100%|##########| 2/2\n100%|##########| 2/2\nloss=0.5\n\ndone\n")
assert out == want, out
print("ok test_runner")
