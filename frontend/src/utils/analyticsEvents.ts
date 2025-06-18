// Analytics event types and utilities

export const AnalyticsEvents = {
  // Page views
  PAGE_VIEW: 'page_view',
  
  // User actions
  BUTTON_CLICK: 'button_click',
  LINK_CLICK: 'link_click',
  
  // Authentication
  USER_SIGNUP: 'user_signup',
  USER_LOGIN: 'user_login',
  USER_LOGOUT: 'user_logout',
  
  // Chat
  CHAT_MESSAGE_SENT: 'chat_message_sent',
  CHAT_MESSAGE_RECEIVED: 'chat_message_received',
  
  // Files
  FILE_UPLOAD: 'file_upload',
  FILE_DELETE: 'file_delete',
  FILE_VIEW: 'file_view',
  
  // Knowledge Base
  KNOWLEDGE_BASE_SEARCH: 'knowledge_base_search',
  KNOWLEDGE_BASE_ADD: 'knowledge_base_add',
  
  // Errors
  ERROR: 'error',
  
  // Feature usage
  FEATURE_USAGE: 'feature_usage',
  
  // Navigation
  TAB_CHANGE: 'tab_change',
  
  // Settings
  SETTINGS_UPDATE: 'settings_update',
} as const;

export type AnalyticsEvent = keyof typeof AnalyticsEvents;

// Helper function to create consistent event properties
export const createEventProperties = (customProps: Record<string, any> = {}) => ({
  timestamp: new Date().toISOString(),
  url: window.location.href,
  path: window.location.pathname,
  ...customProps,
});
