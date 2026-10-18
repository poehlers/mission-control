"""A fake training loop for trying `mc run` (no ML deps): mc run demo --project myproj -- python examples/demo_train.py"""
import random
import time

try:
    from tqdm import tqdm
except ImportError:  # still a decent demo without tqdm
    def tqdm(it, **_):
        return it

print("loading dataset: 48,000 samples, 6,000 held out")
loss = 2.3
for epoch in range(1, 7):
    for _ in tqdm(range(150), desc=f"epoch {epoch}/6", ncols=80, mininterval=1.5):
        time.sleep(0.04)
    loss *= 0.72 + random.random() * 0.05
    print(f"epoch {epoch}: train_loss={loss:.4f} val_acc={1 - loss / 3 + random.random() * 0.01:.3f}")
print("saved checkpoint to checkpoints/best.pt")
