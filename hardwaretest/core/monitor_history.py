"""Bounded time buckets and self-contained SVG plots (no chart dependency)."""

from dataclasses import dataclass
from html import escape
import math


def is_temperature(key: str) -> bool:
    return key.startswith("temperature.") or (key.startswith("gpu.") and key.endswith(".temperature"))


def validated_sensor_limits(values: dict[str, float]) -> dict[str, float]:
    if len(values) > 1024:
        raise ValueError("Zu viele Sensorgrenzen / Too many sensor limits")
    result = {}
    for key, value in values.items():
        if not isinstance(key, str) or len(key) > 256 or not is_temperature(key):
            raise ValueError("Ungültiger Temperatursensor / Invalid temperature sensor")
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not 30 <= value <= 120:
            raise ValueError("Sensorgrenze / Sensor limit: 30–120 °C")
        result[key] = float(value)
    return result


@dataclass(frozen=True)
class Bucket:
    start: float
    end: float
    minimum: float | None
    maximum: float | None
    total: float
    count: int
    gap: bool = False

    def merge(self, other: "Bucket") -> "Bucket":
        minima = [value for value in (self.minimum, other.minimum) if value is not None]
        maxima = [value for value in (self.maximum, other.maximum) if value is not None]
        return Bucket(self.start, other.end, min(minima) if minima else None,
                      max(maxima) if maxima else None, self.total + other.total,
                      self.count + other.count, self.gap or other.gap)


class MetricHistory:
    """Compact all history, preserving extrema and sample-weighted means."""

    def __init__(self, capacity: int = 240):
        if capacity < 2:
            raise ValueError("History capacity must be >= 2")
        self.capacity = capacity
        self.buckets: list[Bucket] = []

    def add(self, elapsed: float, value: float | None) -> None:
        if value is not None and not math.isfinite(value):
            value = None
        self.buckets.append(Bucket(elapsed, elapsed, value, value, value or 0.0,
                                   int(value is not None), value is None))
        if len(self.buckets) > self.capacity:
            old = self.buckets
            self.buckets = [old[i].merge(old[i + 1]) if i + 1 < len(old) else old[i]
                            for i in range(0, len(old), 2)]


def metric_unit(key: str) -> str:
    if is_temperature(key):
        return "°C"
    if key.endswith((".percent", ".utilization")):
        return "%"
    if key.endswith(".power"):
        return "W"
    if key.startswith("fan."):
        return "RPM"
    return ""


def render_chart(key: str, buckets: list[Bucket] | tuple[Bucket, ...],
                 limit: float | None = None, language: str = "de") -> str:
    """Min/max vertical bands, mean line; never draw lines across missing data."""
    en = language == "en"
    unit = metric_unit(key)
    values = [value for bucket in buckets for value in (bucket.minimum, bucket.maximum) if value is not None]
    if not values:
        return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 200">'
                '<rect width="800" height="200" fill="#17191d"/><text x="20" y="90" fill="#eeeeee">'
                + ("No measurements yet" if en else "Noch keine Messwerte") + '</text></svg>')
    minimum, maximum = min(values), max(values)
    if limit is not None:
        minimum, maximum = min(minimum, limit), max(maximum, limit)
    padding = max(1.0, (maximum - minimum) * 0.08)
    minimum -= padding
    maximum += padding
    first = buckets[0].start
    last = max(buckets[-1].end, first + 1)
    scale, time_unit = (1, "s") if last <= 120 else ((60, "min") if last <= 7200 else (3600, "h"))
    # Elapsed seconds avoid misleading wall-clock jumps during a multi-day run.
    def x(seconds):
        return 65 + (seconds - first) / (last - first) * 710

    def y(value):
        return 155 - (value - minimum) / (maximum - minimum) * 115

    lines = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 200" role="img">',
             '<title>' + escape(key) + '</title><rect width="800" height="200" rx="6" fill="#17191d"/>',
             '<g font-family="sans-serif" font-size="12" fill="#eeeeee">',
             '<text x="15" y="22">' + escape(key[:105]) + '</text>',
             f'<text x="4" y="44">{maximum:.1f}</text><text x="4" y="159">{minimum:.1f}</text>',
             f'<text x="4" y="100">{escape(unit)}</text>',
             '<line x1="65" y1="155" x2="775" y2="155" stroke="#666666"/>',
             f'<text x="65" y="174">{first / scale:.2f} {time_unit}</text>',
             f'<text x="700" y="174">{last / scale:.2f} {time_unit}</text>']
    if limit is not None:
        lines.extend([f'<line x1="65" y1="{y(limit):.2f}" x2="775" y2="{y(limit):.2f}" '
                      'stroke="#ff6565" stroke-dasharray="6 4"/>',
                      '<text x="580" y="34">' + ("Limit" if en else "Grenze") + f': {limit:g} {escape(unit)}</text>'])
    previous = None
    for bucket in buckets:
        if not bucket.count:
            previous = None
            continue
        mean = bucket.total / bucket.count
        middle = (bucket.start + bucket.end) / 2
        point = (x(middle), y(mean))
        lines.append(f'<line x1="{point[0]:.2f}" y1="{y(bucket.minimum):.2f}" '
                     f'x2="{point[0]:.2f}" y2="{y(bucket.maximum):.2f}" stroke="#8cc8ff" stroke-width="2"/>')
        if previous is not None and not bucket.gap:
            lines.append(f'<line data-mean="1" x1="{previous[0]:.2f}" y1="{previous[1]:.2f}" '
                         f'x2="{point[0]:.2f}" y2="{point[1]:.2f}" stroke="#43d4b1"/>')
        lines.append(f'<circle cx="{point[0]:.2f}" cy="{point[1]:.2f}" r="1.7" fill="#43d4b1"/>')
        previous = None if bucket.gap else point
    lines.extend(['<text x="65" y="193">' + ("Bands: min/max · line: mean · missing data: gaps · elapsed time"
                  if en else "Bänder: Min/Max · Linie: Mittelwert · fehlende Werte: Lücken · verstrichene Zeit")
                  + '</text></g></svg>'])
    return "".join(lines)
