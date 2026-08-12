#!/usr/bin/env python
"""Migration: normalize units of existing PropertyValues in the database.

Converts:
  kbar         → GPa    (×0.1)
  K / °K       → °C     (-273.15)
  cal/g        → kJ/kg  (×4.184)
  kcal/kg      → kJ/kg  (×4.184)
  MJ/kg        → kJ/kg  (×1000)
  nm           → μm     (×0.001)   particle_size only
  mm           → μm     (×1000)    particle_size only
  g/cc, g/cm3  → g/cm³  (display fix)
  Gpa, gpa     → GPa    (display fix)
  mm·s, mm·s⁻¹ → mm/s   (display fix)

Run once after schema upgrade:
    uv run python scripts/migrate_normalize_units.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger
from app.core.database import SessionLocal
from app.models.orm.domain import PropertyValue
from app.pipeline.domain_extractor import normalize_unit, _PLAUSIBILITY


def main() -> None:
    db = SessionLocal()
    try:
        pvs = db.query(PropertyValue).all()
        logger.info(f"Processing {len(pvs)} property values …")

        updated = 0
        dropped = 0

        for pv in pvs:
            if pv.value_numeric is None or not pv.unit:
                continue

            orig_val = pv.value_numeric
            orig_unit = pv.unit
            new_val, new_unit = normalize_unit(pv.property_name, orig_val, orig_unit)

            if new_val == orig_val and new_unit == orig_unit:
                continue  # nothing to do

            # Plausibility check after conversion
            lo, hi = _PLAUSIBILITY.get(pv.property_name, (-1e18, 1e18))
            if not (lo <= new_val <= hi):
                logger.warning(
                    f"  DROP implausible after conversion: "
                    f"{pv.property_name} {orig_val} {orig_unit} → {new_val:.2f} {new_unit}"
                )
                db.delete(pv)
                dropped += 1
                continue

            logger.info(
                f"  {pv.property_name}: {orig_val} {orig_unit!r} → {new_val:.4g} {new_unit!r}"
            )
            pv.value_numeric = new_val
            pv.unit = new_unit
            pv.value_text = f"{new_val:.4g} {new_unit}"
            updated += 1

        db.commit()
        logger.info(f"Done — {updated} values converted, {dropped} implausible values dropped")
    finally:
        db.close()


if __name__ == "__main__":
    main()
