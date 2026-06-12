import * as fc from 'fast-check'
import type { Variants } from 'motion/react'
import {
  badgeBounce,
  cardEntrance,
  inlineEditEntrance,
  listItemLayout,
  modalEntrance,
  overlayBackdrop,
  pageEnter,
  pageExit,
  prefersReducedMotion,
  reducedMotionInstant,
  reducedPageVariants,
  springBouncy,
  springDefault,
  springGentle,
  springStiff,
  STATUS_FLASH,
  statusColorTransition,
  staggerChildren,
  toastEntrance,
  tweenDefault,
  tweenFast,
  tweenSlow,
} from '@/lib/motion'

describe('motion presets', () => {
  describe('spring presets', () => {
    const springs = [
      { name: 'springDefault', value: springDefault },
      { name: 'springGentle', value: springGentle },
      { name: 'springBouncy', value: springBouncy },
      { name: 'springStiff', value: springStiff },
    ]

    it.each(springs)('$name has valid spring config', ({ value }) => {
      expect(value).toMatchObject({
        type: 'spring',
        stiffness: expect.any(Number) as unknown,
        damping: expect.any(Number) as unknown,
        mass: expect.any(Number) as unknown,
      })
      const v = value as { stiffness: number; damping: number; mass: number }
      expect(v.stiffness).toBeGreaterThan(0)
      expect(v.damping).toBeGreaterThan(0)
      expect(v.mass).toBeGreaterThan(0)
    })
  })

  describe('tween presets', () => {
    const tweens = [
      { name: 'tweenDefault', value: tweenDefault },
      { name: 'tweenSlow', value: tweenSlow },
      { name: 'tweenFast', value: tweenFast },
    ]

    it.each(tweens)('$name has valid tween config', ({ value }) => {
      expect(value).toMatchObject({
        type: 'tween',
        duration: expect.any(Number) as unknown,
      })
      const v = value as { duration: number }
      expect(v.duration).toBeGreaterThan(0)
    })
  })

  describe('new entrance variants (initial/animate/exit pattern)', () => {
    const entranceVariants: Array<{ name: string; value: Variants }> = [
      { name: 'toastEntrance', value: toastEntrance },
      { name: 'overlayBackdrop', value: overlayBackdrop },
      { name: 'modalEntrance', value: modalEntrance },
      { name: 'listItemLayout', value: listItemLayout },
      { name: 'inlineEditEntrance', value: inlineEditEntrance },
    ]

    it.each(entranceVariants)('$name has initial state with opacity 0', ({ value }) => {
      const initial = value['initial'] as { opacity: number }
      expect(initial.opacity).toBe(0)
    })

    it.each(entranceVariants)('$name has animate state with opacity 1', ({ value }) => {
      const animate = value['animate'] as { opacity: number }
      expect(animate.opacity).toBe(1)
    })

    it.each(entranceVariants)('$name has exit state', ({ value }) => {
      expect(value['exit']).toBeDefined()
    })
  })

  describe('cardEntrance (hidden/visible pattern)', () => {
    it('has hidden state with opacity 0', () => {
      const hidden = cardEntrance['hidden'] as { opacity: number }
      expect(hidden.opacity).toBe(0)
    })

    it('has visible state with opacity 1', () => {
      const visible = cardEntrance['visible'] as { opacity: number }
      expect(visible.opacity).toBe(1)
    })
  })

  describe('page transitions', () => {
    it('pageEnter has initial and animate states', () => {
      expect(pageEnter['initial']).toBeDefined()
      expect(pageEnter['animate']).toBeDefined()
    })

    it('pageExit has initial and exit states', () => {
      expect(pageExit['initial']).toBeDefined()
      expect(pageExit['exit']).toBeDefined()
    })

    it('reducedPageVariants has opacity-only transitions', () => {
      const initial = reducedPageVariants['initial'] as { opacity: number }
      const animate = reducedPageVariants['animate'] as { opacity: number }
      expect(initial.opacity).toBe(0)
      expect(animate.opacity).toBe(1)
      // Should not have x/y transforms
      expect(initial).not.toHaveProperty('x')
      expect(initial).not.toHaveProperty('y')
    })
  })

  describe('staggerChildren', () => {
    it('has hidden and visible states', () => {
      expect(staggerChildren['hidden']).toBeDefined()
      expect(staggerChildren['visible']).toBeDefined()
    })
  })

  describe('badgeBounce', () => {
    it('has initial and bounce states', () => {
      expect(badgeBounce['initial']).toBeDefined()
      expect(badgeBounce['bounce']).toBeDefined()
    })
  })

  describe('STATUS_FLASH', () => {
    it('has correct total duration', () => {
      expect(STATUS_FLASH.totalMs).toBe(
        STATUS_FLASH.flashMs + STATUS_FLASH.holdMs + STATUS_FLASH.fadeMs,
      )
    })

    it('all durations are positive', () => {
      expect(STATUS_FLASH.flashMs).toBeGreaterThan(0)
      expect(STATUS_FLASH.holdMs).toBeGreaterThan(0)
      expect(STATUS_FLASH.fadeMs).toBeGreaterThan(0)
    })
  })

  describe('statusColorTransition', () => {
    it('is a tween transition', () => {
      expect(statusColorTransition).toMatchObject({
        type: 'tween',
        duration: expect.any(Number) as unknown,
      })
    })
  })

  describe('reduced motion', () => {
    const originalMatchMedia = window.matchMedia

    afterEach(() => {
      window.matchMedia = originalMatchMedia
    })

    it('reducedMotionInstant has zero duration', () => {
      expect(reducedMotionInstant).toMatchObject({ duration: 0 })
    })

    it('prefersReducedMotion returns false when no preference set', () => {
      window.matchMedia = vi.fn().mockReturnValue({ matches: false })
      expect(prefersReducedMotion()).toBe(false)
    })

    it('prefersReducedMotion returns true when reduced motion preferred', () => {
      window.matchMedia = vi.fn().mockReturnValue({ matches: true })
      expect(prefersReducedMotion()).toBe(true)
    })
  })

  describe('property: all entrance variants have matching structure', () => {
    const entranceNames = [
      'toastEntrance',
      'overlayBackdrop',
      'modalEntrance',
      'listItemLayout',
      'inlineEditEntrance',
    ] as const

    const entranceMap: Record<string, Variants> = {
      toastEntrance,
      overlayBackdrop,
      modalEntrance,
      listItemLayout,
      inlineEditEntrance,
    }

    it('every entrance variant has initial/animate/exit keys', () => {
      fc.assert(
        fc.property(
          fc.constantFrom(...entranceNames),
          (name) => {
            const variant = entranceMap[name]
            if (!variant) return false
            return (
              'initial' in variant &&
              'animate' in variant &&
              'exit' in variant
            )
          },
        ),
      )
    })
  })
})
