# Accessibility

## Requirements

Every interactive element has an accessible label and role. Touch targets are at least 44 by 44 points. Text scales with Dynamic Type and the Android font scale up to 200% without truncating essential content.

## Colour and contrast

Text meets WCAG 2.2 AA contrast. Tide state is never conveyed by colour alone; charts carry labels and patterns as well.

## Motion

Respect the reduce-motion setting: replace animated tide charts with a static chart and disable parallax on the spot header.

## Screen readers

Forecast charts expose a text summary ("High tide 4.2 m at 06:14, low tide 0.8 m at 12:31"). Test every new screen with VoiceOver and TalkBack before release.
