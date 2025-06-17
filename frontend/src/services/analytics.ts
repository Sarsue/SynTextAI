import posthog from 'posthog-js';
import { useEffect } from 'react';

// Extend PostHog type to include flush method
declare module 'posthog-js' {
  interface PostHog {
    flush?: () => void;
  }
}

// Configuration for PostHog
export interface PostHogConfig {
  debugMode?: boolean;
  userId?: string;
  sessionId?: string;
}

/**
 * Initialize PostHog analytics
 * @param config Configuration for PostHog
 */
export function initPostHog(config: PostHogConfig = {}): typeof posthog {
  // Initialize PostHog with API key from environment variables
  const apiKey = process.env.REACT_APP_POST_HOG_API_KEY || '';
  const sessionId = config.sessionId || generateSessionId();
  
  // Initialize PostHog
  posthog.init(apiKey, {
    api_host: 'https://app.posthog.com',
    autocapture: false, // Disable automatic event capture
    capture_pageview: false, // Disable automatic pageview capture
    loaded: (ph) => {
      if (config.userId) {
        ph.identify(config.userId);
      }
      
      // Add session ID as property to all future events
      ph.register({
        session_id: sessionId
      });
      
      if (config.debugMode) {
        ph.debug();
        console.log('[PostHog] Initialized with session ID:', sessionId);
      }
    }
  });
  
  return posthog;
}

/**
 * Generate a random session ID
 */
function generateSessionId(): string {
  return Math.random().toString(36).substring(2, 15) + 
         Math.random().toString(36).substring(2, 15);
}

/**
 * Track a page view
 * @param url The URL of the page
 * @param referrer The referring URL
 */
export function trackPageView(url: string, referrer: string = document.referrer): void {
  posthog.capture('$pageview', {
    $current_url: url,
    $referrer: referrer,
    timestamp: Date.now()
  });
}

/**
 * React hook to use PostHog analytics
 */
export function usePostHog(config?: PostHogConfig): typeof posthog {
  useEffect(() => {
    // Initialize PostHog if not already initialized
    const client = initPostHog(config);
    
    return () => {
      // Flush any pending events on unmount
      if (typeof client.flush === 'function') {
        client.flush();
      }
    };
  }, []);
  
  return posthog;
}

/**
 * Export posthog instance for direct use
 */
export default posthog;
