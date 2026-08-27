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
  const apiKey = import.meta.env.VITE_POST_HOG_API_KEY || '';
  const sessionId = config.sessionId || generateSessionId();
  
  // Initialize PostHog
  posthog.init(apiKey, {
    // The regional host, not app.posthog.com. app.posthog.com is the dashboard
    // origin; posthog-js accepts it and then resolves it to us.i.posthog.com for
    // events and us-assets.i.posthog.com for recorder.js anyway. Naming the
    // regional host directly means the CSP has to allow the two origins the
    // browser actually contacts, and nothing else. This project is on PostHog
    // Cloud US; a move to EU is a change here and in the CSP in api/app.py.
    api_host: 'https://us.i.posthog.com',
    // Where "view in PostHog" links point. The dashboard still lives on
    // app.posthog.com, which the ingestion host is not.
    ui_host: 'https://app.posthog.com',
    autocapture: false, // Disable automatic event capture
    capture_pageview: false, // Disable automatic pageview capture
    loaded: (ph) => {
      if (config.userId) {
        ph.identify(config.userId);
      }
      
      // Add session ID as property to all future events
      ph.register({
        session_id: sessionId,
        environment: currentEnvironment()
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
 * Which deployment an event came from, so local clicking does not read as
 * customer behaviour on the dashboard.
 *
 * Decided by hostname, not by import.meta.env.DEV. The local container serves a
 * production build, so DEV is false there and every event fired while testing
 * would be counted as a real one. The hostname is the only thing that actually
 * differs.
 */
function currentEnvironment(): string {
  const host = window.location.hostname;
  if (host === 'syntextai.com' || host.endsWith('.syntextai.com')) {
    return 'production';
  }
  return 'development';
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
