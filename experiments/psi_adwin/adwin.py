"""Small ADWIN for a scalar error stream.

``river`` is not installed in this environment. This is the Bifet-Gavalda
cut: two halves of the window are split when their means differ by more than

    epsilon = sqrt( ln(4 * n / delta) / (2 * m) )

where ``m`` is the harmonic-style size ``1 / (1/n0 + 1/n1)`` and ``n`` is the
current window length. ``delta`` is a confidence parameter, not a team
decision. The experiment default is 0.002.
"""

from __future__ import annotations

import math


class ADWIN:
    def __init__(self, delta: float = 0.002, min_window: int = 32, min_side: int = 16):
        if not 0.0 < delta < 1.0:
            raise ValueError("delta must be between 0 and 1")
        if min_side < 1 or min_window < 2 * min_side:
            raise ValueError("need min_side >= 1 and min_window >= 2 * min_side")
        self.delta = float(delta)
        self.min_window = int(min_window)
        self.min_side = int(min_side)
        self.window: list[float] = []

    def update(self, value: float) -> bool:
        self.window.append(float(value))
        changed = False
        while self._cut_once():
            changed = True
        return changed

    def _cut_once(self) -> bool:
        values = self.window
        count = len(values)
        if count < self.min_window:
            # A shorter window cannot host a stable two-sided mean test (default 32).
            return False
        prefix = [0.0]
        for item in values:
            prefix.append(prefix[-1] + item)
        for split in range(self.min_side, count - self.min_side + 1):
            left, right = split, count - split
            mean_left = prefix[split] / left
            mean_right = (prefix[count] - prefix[split]) / right
            harmonic = 1.0 / (1.0 / left + 1.0 / right)
            epsilon = math.sqrt(math.log(4.0 * count / self.delta) / (2.0 * harmonic))
            if abs(mean_left - mean_right) > epsilon:
                del values[:split]
                return True
        return False
