import type { ViewProps } from 'react-native';
import { requireNativeModule, requireNativeViewManager } from 'expo-modules-core';

type LabNative = {
  args: string[];
  begin(name: string, msg: string): number;
  end(handle: number, msg: string): void;
  event(name: string, msg: string): void;
  footprintMB(): number;
  sinceProcessStartMs(): number;
  startHitches(): void;
  exit(): void;
  autoscroll(speed: number, distance: number, handle: number): Promise<string>;
};

export default requireNativeModule<LabNative>('Lab');

/// Ends signpost interval `handle` / emits `event` once this view is mounted in the window (see LabProbeView).
export const LabProbe = requireNativeViewManager<ViewProps & { handle?: number; event?: 'ready' }>('Lab');
