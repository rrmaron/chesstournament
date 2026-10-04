/**
 * Tests for PlayerScreen.js's rating-impact math (beads mychesspairings-8jb,
 * part 2 of 2). These were previously unexported module-local functions;
 * exporting them (no behavior change) is what makes this file possible --
 * same pattern as extracting _refresh_live_sources out of the Python harvest
 * loop. eloImpact/uscfK/fideK are pure and deterministic, with real
 * correctness risk (a wrong K-factor or expected-score formula misleads a
 * player about how a game would affect their rating), so plain Jest
 * (no component rendering) is enough here.
 */
import { eloImpact, uscfK, fideK } from '../screens/PlayerScreen';

describe('eloImpact', () => {
  test('equal ratings give a 50% expected score and symmetric win/loss', () => {
    expect(eloImpact(1500, 1500, 32)).toEqual({ win: 16, draw: 0, loss: -16, pct: 50 });
  });

  test('a lower-rated opponent means a smaller win bonus than a higher-rated one', () => {
    const vsStronger = eloImpact(1500, 1700, 32);
    const vsWeaker = eloImpact(1700, 1500, 32);
    expect(vsStronger).toEqual({ win: 24.3, draw: 8.3, loss: -7.7, pct: 24 });
    expect(vsWeaker).toEqual({ win: 7.7, draw: -8.3, loss: -24.3, pct: 76 });
  });

  test('win/draw/loss deltas are always exactly k apart', () => {
    const r = eloImpact(1500, 1700, 32);
    expect(Math.round((r.win - r.draw) * 10) / 10).toBe(16);
    expect(Math.round((r.draw - r.loss) * 10) / 10).toBe(16);
  });

  test('a larger K-factor scales the impact proportionally', () => {
    const k16 = eloImpact(1500, 1500, 16);
    const k32 = eloImpact(1500, 1500, 32);
    expect(k32.win).toBe(k16.win * 2);
  });
});

describe('uscfK', () => {
  test.each([
    [1500, 32],
    [2099, 32],
    [2100, 24],   // boundary: >=2100 drops to 24
    [2399, 24],
    [2400, 16],   // boundary: >=2400 drops to 16
    [2500, 16],
  ])('uscfK(%i) === %i', (rating, expected) => {
    expect(uscfK(rating)).toBe(expected);
  });
});

describe('fideK', () => {
  test.each([
    [1500, 40],
    [1599, 40],
    [1600, 20],   // boundary: >=1600 drops to 20
    [2399, 20],
    [2400, 10],   // boundary: >=2400 drops to 10
  ])('fideK(%i) === %i', (rating, expected) => {
    expect(fideK(rating)).toBe(expected);
  });
});
