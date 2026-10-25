# Design system

## Spacing

Spacing uses a 4-point scale: 4, 8, 12, 16, 24, 32, 48. Do not use other values; snap to the nearest step.

## Colour tokens

The primary brand colour token is `color.brand.600`. The old `color.primary` token is deprecated and will be removed in 3.0; do not use it in new code.

## Typography

Type ramps are defined in `typography.ts`; screens must use the named styles (`title`, `body`, `caption`) instead of raw font sizes.

## Touch targets

Touch targets are at least 48 by 48 points, including icon buttons in toolbars and list row accessories.
