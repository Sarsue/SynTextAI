import React, { createContext, useContext, useEffect } from 'react';
import posthog, { initPostHog, PostHogConfig, usePostHog as usePostHogHook } from '../services/analytics';

// Create a context for PostHog direct access
const PostHogContext = createContext<typeof posthog | null>(null);

interface AnalyticsProviderProps {
  children: React.ReactNode;
  config?: PostHogConfig;
}

/**
 * Provider component that initializes analytics and makes it available via context
 * Wrap your app with this to enable analytics tracking
 */
export const AnalyticsProvider: React.FC<AnalyticsProviderProps> = ({ 
  children, 
  config = {} 
}) => {
  // Initialize analytics on mount
  useEffect(() => {
    const defaultConfig: PostHogConfig = {
      debugMode: process.env.NODE_ENV === 'development',
      ...config
    };

    // Initialize PostHog with merged config
    initPostHog(defaultConfig);
    
    // Cleanup on unmount - flush events
    return () => {
      if (typeof posthog.flush === 'function') {
        posthog.flush();
      }
    };
  }, [config.userId]); // Re-run when userId changes

  return (
    <PostHogContext.Provider value={posthog}>
      {children}
    </PostHogContext.Provider>
  );
};

// Custom hook to use PostHog directly
export const usePostHog = () => {
  const posthogClient = useContext(PostHogContext);
  
  if (!posthogClient) {
    throw new Error('usePostHog must be used within an AnalyticsProvider');
  }
  
  return posthogClient;
};

// Keep legacy hook name for backward compatibility
export const useAnalytics = usePostHog;

// Higher Order Component to track page views
export const withPageTracking = <P extends object>(
  Component: React.ComponentType<P>,
  pageName?: string
) => {
  const WithPageTracking: React.FC<P> = (props) => {
    const posthogClient = usePostHog();
    
    useEffect(() => {
      // Track page view in PostHog
      const path = pageName || window.location.pathname;
      
      posthogClient.capture('$pageview', {
        $current_url: path,
        $title: document.title,
      });
      
    }, []);
    
    return <Component {...props} />;
  };
  
  return WithPageTracking;
};

// Track user actions with this HOC
export const withActionTracking = <P extends object>(
  Component: React.ComponentType<P>,
  actionCategory?: string
) => {
  const WithActionTracking: React.FC<P & { trackAction?: (action: string, properties?: Record<string, any>) => void }> = (props) => {
    const posthogClient = usePostHog();
    
    // Create a wrapper function to track specific actions
    const trackAction = (action: string, properties: Record<string, any> = {}) => {
      // Track in PostHog with category metadata
      posthogClient.capture(action, {
        category: actionCategory || 'user_action',
        path: window.location.pathname,
        timestamp: Date.now(),
        ...properties
      });
    };
    
    return <Component {...props} trackAction={trackAction} />;
  };
  
  return WithActionTracking;
};

export default AnalyticsProvider;

// Example usage tracker for features
export const trackFeatureUsage = (featureName: string, properties: Record<string, any> = {}) => {
  try {
    // Track feature usage in PostHog
    posthog.capture(`feature_${featureName}`, {
      type: 'feature_usage',
      feature: featureName,
      timestamp: Date.now(),
      path: window.location.pathname,
      ...properties
    });
  } catch (error) {
    console.error('Error tracking feature usage:', error);
  }
};
