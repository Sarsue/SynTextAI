import { useCallback } from 'react';
import { usePostHog } from '../components/AnalyticsProvider';

type CaptureResult = void | { event: string; properties: Record<string, any> };

export const useAnalytics = () => {
  const posthog = usePostHog();
  
  const capture = useCallback(async (event: string, properties: Record<string, any> = {}): Promise<CaptureResult> => {
    if (!posthog) {
      console.warn('Analytics not initialized - event not captured:', event);
      return { event, properties };
    }
    
    try {
      const result = posthog.capture(event, {
        ...properties,
        timestamp: new Date().toISOString(),
      });
      
      // Handle both Promise and non-Promise return types
      return result instanceof Promise ? await result : { event, properties };
    } catch (error) {
      console.error('Analytics capture failed:', error);
      return { event, properties };
    }
  }, [posthog]);

  const identify = useCallback((userId: string, traits?: Record<string, any>): void => {
    if (!posthog) {
      console.warn('Analytics not initialized - identify skipped');
      return;
    }
    
    try {
      posthog.identify(userId, traits);
    } catch (error) {
      console.error('Analytics identify failed:', error);
    }
  }, [posthog]);

  const reset = useCallback((): void => {
    if (!posthog) {
      console.warn('Analytics not initialized - reset skipped');
      return;
    }
    
    try {
      posthog.reset();
    } catch (error) {
      console.error('Analytics reset failed:', error);
    }
  }, [posthog]);

  return {
    capture,
    identify,
    reset,
  };
};

export default useAnalytics;
