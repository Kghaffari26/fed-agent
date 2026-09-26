"""Per-indicator transforms (§5.1) and deterministic regime labels (§5.4).

Every function takes an ascending-by-date `list[Observation]` (one entry per
expected period, `value=None` for a genuinely missing reading — see
fetch_fred.parse_observation_value) and returns the value **as of the last
observation**, at full precision. Rounding only happens at publish time
(§5.1), so nothing here rounds.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from agents.macro.config import Frequency, Transform
from agents.macro.fetch_fred import Observation

WEEKLY_YOY_TARGET_DAYS = 364
WEEKLY_YOY_TOLERANCE_DAYS = 10


def _nonnull_at(series: list[Observation], index: int) -> float | None:
    if -len(series) <= index < len(series):
        return series[index].value
    return None


def _latest(series: list[Observation]) -> float | None:
    return _nonnull_at(series, -1)


def yoy_pct(series: list[Observation], frequency: Frequency) -> float | None:
    latest = _latest(series)
    if latest is None:
        return None
    if frequency == "monthly":
        prior = _nonnull_at(series, -13)
    elif frequency == "quarterly":
        prior = _nonnull_at(series, -5)
    elif frequency == "weekly":
        prior = _nearest_weekly_year_ago(series)
    else:
        return None
    if prior is None or prior == 0:
        return None
    return (latest / prior - 1) * 100


def _nearest_weekly_year_ago(series: list[Observation]) -> float | None:
    if not series:
        return None
    latest_date = series[-1].date
    target = latest_date - timedelta(days=WEEKLY_YOY_TARGET_DAYS)
    best: Observation | None = None
    best_distance: int | None = None
    for obs in series[:-1]:
        if obs.value is None:
            continue
        distance = abs((obs.date - target).days)
        if distance <= WEEKLY_YOY_TOLERANCE_DAYS and (best_distance is None or distance < best_distance):
            best, best_distance = obs, distance
    return best.value if best else None


def mom_pct(series: list[Observation]) -> float | None:
    latest = _nonnull_at(series, -1)
    prior = _nonnull_at(series, -2)
    if latest is None or prior is None or prior == 0:
        return None
    return (latest / prior - 1) * 100


def mom_diff(series: list[Observation]) -> float | None:
    latest = _nonnull_at(series, -1)
    prior = _nonnull_at(series, -2)
    if latest is None or prior is None:
        return None
    return latest - prior


def ann_3m_pct(series: list[Observation]) -> float | None:
    latest = _nonnull_at(series, -1)
    prior = _nonnull_at(series, -4)
    if latest is None or prior is None or prior == 0:
        return None
    return ((latest / prior) ** 4 - 1) * 100


def _trailing_nonnull_values(series: list[Observation], count: int) -> list[float] | None:
    """The `count` most recent non-None values, walking back from the end.

    Returns None if fewer than `count` values are available at all.
    """
    values: list[float] = []
    for obs in reversed(series):
        if obs.value is not None:
            values.append(obs.value)
        if len(values) == count:
            break
    if len(values) < count:
        return None
    values.reverse()
    return values


def avg_3(series: list[Observation]) -> float | None:
    values = _trailing_nonnull_values(series, 3)
    return sum(values) / 3 if values else None


def avg_4w(series: list[Observation]) -> float | None:
    values = _trailing_nonnull_values(series, 4)
    return sum(values) / 4 if values else None


def change_pp(series: list[Observation]) -> float | None:
    latest = _nonnull_at(series, -1)
    prior = _nonnull_at(series, -2)
    if latest is None or prior is None:
        return None
    return latest - prior


def level(series: list[Observation]) -> float | None:
    return _latest(series)


_DISPATCH = {
    "yoy_pct": lambda series, frequency: yoy_pct(series, frequency),
    "mom_pct": lambda series, frequency: mom_pct(series),
    "mom_diff": lambda series, frequency: mom_diff(series),
    "ann_3m_pct": lambda series, frequency: ann_3m_pct(series),
    "avg_3": lambda series, frequency: avg_3(series),
    "avg_4w": lambda series, frequency: avg_4w(series),
    "change_pp": lambda series, frequency: change_pp(series),
    "level": lambda series, frequency: level(series),
}


def apply_transform(name: Transform, series: list[Observation], frequency: Frequency) -> float | None:
    return _DISPATCH[name](series, frequency)


def rolling_mean_series(series: list[Observation], window: int) -> list[float | None]:
    """A same-length series of the trailing `window`-period mean at each point.

    None wherever fewer than `window` non-None values are available up to and
    including that point, or wherever a value in the window is missing.
    """
    out: list[float | None] = []
    for i in range(len(series)):
        window_slice = series[max(0, i - window + 1) : i + 1]
        if len(window_slice) < window or any(o.value is None for o in window_slice):
            out.append(None)
        else:
            out.append(sum(o.value for o in window_slice) / window)
    return out


# --------------------------------------------------------------------------
# §5.4 Regimes — deterministic labels
# --------------------------------------------------------------------------

InflationLabel = Literal["Cooling", "Heating", "Steady"]
LaborLabel = Literal["Sahm rule triggered", "Softening", "Stable"]
GrowthLabel = Literal["Contracting", "Slow", "Moderate", "Strong"]
PolicyLabel = Literal["Cutting", "Hiking", "Holding"]
CurveLabel = Literal["Inverted", "Flat", "Normal"]

INFLATION_REGIME_BAND = 0.3
SAHM_TRIGGERED = 0.50
SAHM_SOFTENING = 0.30
PAYROLLS_SOFTENING_THRESHOLD = 50.0  # thousands, 3-month average


def inflation_regime(core_pce_yoy: float, core_pce_ann_3m: float) -> InflationLabel:
    # Round the comparison, not the inputs, so real boundary values (e.g. an
    # ann_3m exactly 0.3pp below yoy) aren't misclassified by float noise.
    diff = round(core_pce_ann_3m - core_pce_yoy, 6)
    if diff < -INFLATION_REGIME_BAND:
        return "Cooling"
    if diff > INFLATION_REGIME_BAND:
        return "Heating"
    return "Steady"


def labor_regime(unrate_avg3_series: list[float | None], payrolls_avg3: float | None) -> LaborLabel:
    """`unrate_avg3_series` is avg3(UNRATE) aligned to each month, latest last."""
    current = unrate_avg3_series[-1] if unrate_avg3_series else None
    if current is not None:
        prior_12 = [v for v in unrate_avg3_series[-13:-1] if v is not None]
        if prior_12:
            sahm = round(current - min(prior_12), 6)
            if sahm >= SAHM_TRIGGERED:
                return "Sahm rule triggered"
            if sahm >= SAHM_SOFTENING:
                return "Softening"
    if payrolls_avg3 is not None and payrolls_avg3 < PAYROLLS_SOFTENING_THRESHOLD:
        return "Softening"
    return "Stable"


def growth_regime(real_gdp_annualized: float) -> GrowthLabel:
    if real_gdp_annualized < 0:
        return "Contracting"
    if real_gdp_annualized <= 1.5:
        return "Slow"
    if real_gdp_annualized <= 3:
        return "Moderate"
    return "Strong"


def curve_regime(spread_10y2y: float) -> CurveLabel:
    if spread_10y2y < 0:
        return "Inverted"
    if spread_10y2y <= 0.5:
        return "Flat"
    return "Normal"


def curve_last_sign_change(spread_series: list[Observation]) -> date | None:
    """The date of the most recent sign flip in a spread series (e.g. T10Y2Y)."""
    last_sign: int | None = None
    last_change: date | None = None
    for obs in spread_series:
        if obs.value is None or obs.value == 0:
            continue
        sign = 1 if obs.value > 0 else -1
        if last_sign is not None and sign != last_sign:
            last_change = obs.date
        last_sign = sign
    return last_change


# --------------------------------------------------------------------------
# Per-indicator values and whole series (§5.1, §5.2)
# --------------------------------------------------------------------------


def diff_series(series: list[Observation]) -> list[Observation]:
    """x_t - x_{t-1} at every point after the first (None if either side is missing)."""
    out = []
    for prev, cur in zip(series, series[1:], strict=False):
        value = None if prev.value is None or cur.value is None else cur.value - prev.value
        out.append(Observation(date=cur.date, value=value))
    return out


def indicator_value(
    name: Transform, series: list[Observation], frequency: Frequency, *, primary: Transform
) -> float | None:
    """`apply_transform`, except that `avg_3` next to a `mom_diff` primary (payrolls) is
    the 3-month average *change* (§2: "MoM change (thousands), 3-mo avg"), not the
    average level."""
    if name == "avg_3" and primary == "mom_diff":
        return avg_3(diff_series(series))
    return apply_transform(name, series, frequency)


def transform_series(
    name: Transform,
    series: list[Observation],
    frequency: Frequency,
    *,
    primary: Transform,
    since: date | None = None,
) -> list[Observation]:
    """The transform evaluated as of every observation dated on/after `since`."""
    if name == "level":
        return [o for o in series if since is None or o.date >= since]
    out = []
    for i, obs in enumerate(series):
        if since is not None and obs.date < since:
            continue
        out.append(
            Observation(
                date=obs.date, value=indicator_value(name, series[: i + 1], frequency, primary=primary)
            )
        )
    return out


def resample_weekly_friday(series: list[Observation]) -> list[Observation]:
    """Daily -> weekly (Friday close, §5.2): the last non-None value of each Mon-Fri
    week, dated that week's Friday."""
    weeks: dict[date, float] = {}
    for obs in series:
        if obs.value is None:
            continue
        # Mon-Fri map forward to that Friday; a weekend print belongs to the week just ended.
        friday = obs.date + timedelta(days=4 - obs.date.weekday())
        weeks[friday] = obs.value
    return [Observation(date=d, value=v) for d, v in sorted(weeks.items())]


def resample_month_end(series: list[Observation]) -> list[Observation]:
    """Daily/weekly -> monthly: the last non-None value in each calendar month, dated
    at that month's last observation."""
    months: dict[tuple[int, int], Observation] = {}
    for obs in series:
        if obs.value is not None:
            months[(obs.date.year, obs.date.month)] = obs
    return [months[k] for k in sorted(months)]


def value_on_or_before(series: list[Observation], day: date) -> Observation | None:
    """The latest non-None observation dated on/before `day`."""
    best = None
    for obs in series:
        if obs.date > day:
            break
        if obs.value is not None:
            best = obs
    return best


def inversion_periods(
    spread: list[Observation], *, min_observations: int = 5
) -> list[tuple[date, date | None]]:
    """Stretches where the spread stayed below zero for at least `min_observations`
    readings. The last period's end is None while it is still ongoing."""
    periods: list[tuple[date, date | None]] = []
    run: list[Observation] = []
    for obs in spread:
        if obs.value is None:
            continue
        if obs.value < 0:
            run.append(obs)
            continue
        if len(run) >= min_observations:
            periods.append((run[0].date, run[-1].date))
        run = []
    if len(run) >= min_observations:
        periods.append((run[0].date, None))
    return periods


def confirmed_sign_change(spread: list[Observation], *, confirmation: int = 5) -> tuple[date, date] | None:
    """The most recent sign change that then held for `confirmation` consecutive
    readings: returns (change_date, confirmed_on), where confirmed_on is the date of the
    `confirmation`-th reading with the new sign (§5.6's 5-day confirmation)."""
    values = [o for o in spread if o.value is not None and o.value != 0]
    result = None
    for i in range(1, len(values)):
        if (values[i].value > 0) == (values[i - 1].value > 0):
            continue
        window = values[i : i + confirmation]
        if len(window) == confirmation and all((o.value > 0) == (values[i].value > 0) for o in window):
            result = (values[i].date, window[-1].date)
    return result
