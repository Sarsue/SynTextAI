import { useCallback } from 'react';
import { usePostHog } from '../components/AnalyticsProvider';

type CaptureResult = void | { event: string; properties: Record<string, any> };

export const useAnalytics = () => {
  const posthog = usePostHog();
  
  const capture = useCallback((event: string, properties: Record<string, any> = {}) => {
    if (!posthog) {
      if (process.env.NODE_ENV === 'development') {
        console.log('[Analytics] Not initialized - event not captured:', event, properties);
      }
      return;
    }
    
    try {
      // Don't await to make it non-blocking
      const result = posthog.capture(event, {
        ...properties,
        timestamp: new Date().toISOString(),
      });
      
      // Handle both Promise and non-Promise return types
      if (result instanceof Promise) {
        result.catch((error: Error) => {
          console.error('[Analytics] Capture failed:', error);
        });
      }
    } catch (error) {
      console.error('[Analytics] Capture error:', error);
    }
  }, [posthog]);

  const identify = useCallback((userId: string, traits?: Record<string, any>) => {
    if (!posthog) {
      if (process.env.NODE_ENV === 'development') {
        console.log('[Analytics] Not initialized - identify skipped');
      }
      return;
    }
    
    try {
      posthog.identify(userId, traits);
      if (process.env.NODE_ENV === 'development') {
        console.log('[Analytics] Identified user:', userId);
      }
    } catch (error) {
      console.error('[Analytics] Identify failed:', error);
    }
  }, [posthog]);

  const reset = useCallback(() => {
    if (!posthog) {
      if (process.env.NODE_ENV === 'development') {
        console.log('[Analytics] Not initialized - reset skipped');
      }
      return;
    }
    
    try {
      posthog.reset();
      if (process.env.NODE_ENV === 'development') {
        console.log('[Analytics] Reset');
      }
    } catch (error) {
      console.error('[Analytics] Reset failed:', error);
    }
  }, [posthog]);

  return {
    capture,
    identify,
    reset,
  };
};

export default useAnalytics;
