import { useCallback, useEffect, useRef, useState } from 'react';
import { getAuth } from 'firebase/auth';
import { WebSocketStatus } from '../UserContext';
import { KnownWebSocketMessage } from '../types/websocketTypes';

const HEARTBEAT_INTERVAL = 30000;
const RECONNECT_DELAY = 5000;
const MAX_RECONNECT_ATTEMPTS = 5;

export const useWebSocket = (
  userId: string | undefined,
  onMessage: (message: KnownWebSocketMessage) => void,
  onStatusChange?: (status: WebSocketStatus) => void
) => {
  const [status, setStatus] = useState<WebSocketStatus>('disconnected');
  const ws = useRef<WebSocket | null>(null);
  const reconnectAttempts = useRef(0);
  const heartbeatInterval = useRef<NodeJS.Timeout | null>(null);
  const reconnectTimeout = useRef<NodeJS.Timeout | null>(null);
  const messageQueue = useRef<any[]>([]);

  const setStatusWithCallback = useCallback((newStatus: WebSocketStatus) => {
    setStatus(newStatus);
    onStatusChange?.(newStatus);
  }, [onStatusChange]);

  const setupHeartbeat = useCallback((socket: WebSocket) => {
    if (heartbeatInterval.current) {
      clearInterval(heartbeatInterval.current);
    }

    heartbeatInterval.current = setInterval(() => {
      if (socket.readyState === WebSocket.OPEN) {
        try {
          socket.send(JSON.stringify({ type: 'ping', timestamp: Date.now() }));
        } catch (error) {
          console.error('Error sending heartbeat:', error);
        }
      }
    }, HEARTBEAT_INTERVAL);
  }, []);

  const processMessageQueue = useCallback(() => {
    if (!ws.current || ws.current.readyState !== WebSocket.OPEN) return;

    while (messageQueue.current.length > 0) {
      const message = messageQueue.current.shift();
      try {
        ws.current.send(JSON.stringify(message));
      } catch (error) {
        console.error('Error sending queued message:', error);
        messageQueue.current.unshift(message);
        break;
      }
    }
  }, []);

  const connect = useCallback(async () => {
    // Prevent multiple connection attempts
    if (!userId || ws.current?.readyState === WebSocket.OPEN || ws.current?.readyState === WebSocket.CONNECTING) {
      return;
    }

    // Clean up any existing connection
    if (ws.current) {
      ws.current.onopen = null;
      ws.current.onmessage = null;
      ws.current.onerror = null;
      ws.current.onclose = null;
      if (ws.current.readyState === WebSocket.OPEN) {
        ws.current.close();
      }
      ws.current = null;
    }

    setStatusWithCallback('connecting');

    try {
      const token = await getAuth().currentUser?.getIdToken(true); // Force token refresh
      if (!token) {
        throw new Error('No auth token available');
      }

      const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
      const wsUrl = `${protocol}://${window.location.host}/ws/${userId}?token=${encodeURIComponent(token)}&v=${Date.now()}`;
      
      const socket = new WebSocket(wsUrl);
      ws.current = socket;

      socket.onopen = () => {
        console.log('WebSocket connected');
        reconnectAttempts.current = 0;
        setStatusWithCallback('connected');
        setupHeartbeat(socket);
        processMessageQueue();
      };

      socket.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);
          if (message.type === 'pong') {
            return;
          }
          onMessage(message);
        } catch (error) {
          console.error('Error processing WebSocket message:', error);
        }
      };

      socket.onerror = (error) => {
        console.error('WebSocket error:', error);
      };

      socket.onclose = (event) => {
        console.log(`WebSocket closed: ${event.code} ${event.reason}`);
        
        // Clean up heartbeat
        if (heartbeatInterval.current) {
          clearInterval(heartbeatInterval.current);
          heartbeatInterval.current = null;
        }

        // Don't reconnect if we're in the process of disconnecting
        if (status === 'disconnected') {
          return;
        }

        // Don't reconnect for auth errors
        if (event.code === 4001) {
          console.error('Authentication failed - not reconnecting');
          setStatusWithCallback('disconnected');
          return;
        }

        // Attempt to reconnect with exponential backoff
        if (reconnectAttempts.current < MAX_RECONNECT_ATTEMPTS) {
          const delay = Math.min(RECONNECT_DELAY * Math.pow(2, reconnectAttempts.current), 30000);
          console.log(`Reconnecting in ${delay}ms (attempt ${reconnectAttempts.current + 1}/${MAX_RECONNECT_ATTEMPTS})`);
          
          reconnectAttempts.current += 1;
          setStatusWithCallback('reconnecting');
          
          reconnectTimeout.current = setTimeout(() => {
            if (userId) {
              connect();
            }
          }, delay);
        } else {
          console.error('Max reconnection attempts reached');
          setStatusWithCallback('disconnected');
        }
      };
    } catch (error) {
      console.error('WebSocket connection error:', error);
      setStatusWithCallback('disconnected');
      
      // Schedule a reconnection attempt
      if (reconnectAttempts.current < MAX_RECONNECT_ATTEMPTS) {
        const delay = Math.min(RECONNECT_DELAY * Math.pow(2, reconnectAttempts.current), 30000);
        reconnectAttempts.current += 1;
        reconnectTimeout.current = setTimeout(() => {
          if (userId) {
            connect();
          }
        }, delay);
      }
    }
  }, [userId, status, onMessage, setStatusWithCallback, setupHeartbeat, processMessageQueue]);

  const disconnect = useCallback(() => {
    console.log('Disconnecting WebSocket...');
    
    // Clear any pending reconnection attempts
    if (reconnectTimeout.current) {
      clearTimeout(reconnectTimeout.current);
      reconnectTimeout.current = null;
    }

    // Clear heartbeat interval
    if (heartbeatInterval.current) {
      clearInterval(heartbeatInterval.current);
      heartbeatInterval.current = null;
    }

    // Close WebSocket connection if it exists
    if (ws.current) {
      try {
        // Remove all event listeners to prevent memory leaks
        ws.current.onopen = null;
        ws.current.onmessage = null;
        ws.current.onerror = null;
        ws.current.onclose = null;
        
        // Only try to close if the connection is open or connecting
        if (ws.current.readyState === WebSocket.OPEN || ws.current.readyState === WebSocket.CONNECTING) {
          ws.current.close(1000, 'User disconnected');
        }
      } catch (error) {
        console.error('Error closing WebSocket:', error);
      } finally {
        ws.current = null;
      }
    }

    setStatusWithCallback('disconnected');
    console.log('WebSocket disconnected');
  }, [setStatusWithCallback]);

  const send = useCallback((message: any) => {
    if (ws.current?.readyState === WebSocket.OPEN) {
      try {
        ws.current.send(JSON.stringify(message));
        return true;
      } catch (error) {
        console.error('Error sending WebSocket message:', error);
        messageQueue.current.push(message);
        return false;
      }
    } else {
      messageQueue.current.push(message);
      return false;
    }
  }, []);

  // Track if the component is mounted
  const isMounted = useRef(true);

  useEffect(() => {
    // Set mounted flag to true when component mounts
    isMounted.current = true;
    
    // Only connect if we have a user ID and we're not already connected/connecting
    if (userId) {
      console.log('Connecting WebSocket for user:', userId);
      connect();
    } else {
      console.log('No user ID, disconnecting WebSocket');
      disconnect();
    }

    // Cleanup function that runs when the component unmounts or when dependencies change
    return () => {
      console.log('Cleaning up WebSocket');
      isMounted.current = false;
      disconnect();
    };
  }, [userId]); // Only depend on userId to prevent unnecessary reconnects

  return { status, send, disconnect, connect };
};
