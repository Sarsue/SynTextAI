// UserContext.tsx

import React, { createContext, useContext, useState, ReactNode, useEffect } from 'react';
import { User as FirebaseUser } from 'firebase/auth';
import { io, Socket } from 'socket.io-client';

// Define the type for UserSettings
interface UserSettings {
    comprehensionLevel: string;
    selectedLanguage: string;
}

// Define the type for SubscriptionData
interface SubscriptionData {
    subscription_status: string;
    card_last4?: string;
    card_brand?: string;
    card_exp_month?: string;
    card_exp_year?: string;
    trial_end?: string;
}

// Define the type for UserContext
interface UserContextType {
    user: FirebaseUser | null; // Add user state
    setUser: (user: FirebaseUser | null) => void; // Method to set user
    darkMode: boolean;
    toggleDarkMode: () => void;
    setDarkMode: (darkMode: boolean) => void;
    userSettings: UserSettings;
    setUserSettings: (settings: UserSettings) => void;
    isPollingFiles: boolean;
    setIsPollingFiles: (isPollingFile: boolean) => void;
    isPollingMessages: boolean;
    setIsPollingMessages: (isPollingMsg: boolean) => void;
    subscriptionStatus: string | null;
    setSubscriptionStatus: (status: string | null) => void;
    fetchSubscriptionStatus: () => void;
    subscriptionData: SubscriptionData | null;
    setSubscriptionData: (data: SubscriptionData | null) => void;
    registerUserInBackend: (user: FirebaseUser) => Promise<void>;
    socket: Socket | null;
    initializeWebSocket: () => Promise<void>;
    disconnectWebSocket: () => void;
}

// Create the UserContext with initial values
const UserContext = createContext<UserContextType>({
    user: null,
    setUser: () => { },
    darkMode: false,
    toggleDarkMode: () => { },
    setDarkMode: () => { },
    userSettings: {
        comprehensionLevel: 'Beginner',
        selectedLanguage: 'English',
    },
    setUserSettings: () => { },
    isPollingFiles: false,
    setIsPollingFiles: () => { },
    isPollingMessages: false,
    setIsPollingMessages: () => { },
    subscriptionStatus: null,
    setSubscriptionStatus: () => { },
    fetchSubscriptionStatus: () => { },
    subscriptionData: null,
    setSubscriptionData: () => { },
    registerUserInBackend: async () => { },
    socket: null,
    initializeWebSocket: async () => { },
    disconnectWebSocket: () => { },
});

// Define UserProvider component
export const UserProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
    const [user, setUser] = useState<FirebaseUser | null>(null); // Manage user state here
    const [darkMode, setDarkMode] = useState<boolean>(false);
    const [userSettings, setUserSettings] = useState<UserSettings>({
        comprehensionLevel: 'Beginner',
        selectedLanguage: 'English',
    });
    const [isPollingFiles, setIsPollingFiles] = useState<boolean>(false);
    const [isPollingMessages, setIsPollingMessages] = useState<boolean>(false);
    const [subscriptionStatus, setSubscriptionStatus] = useState<string | null>(null);
    const [subscriptionData, setSubscriptionData] = useState<SubscriptionData | null>(null);
    const [socket, setSocket] = useState<Socket | null>(null);

    const SOCKET_RECONNECTION_ATTEMPTS = 5;
    const SOCKET_RECONNECTION_DELAY = 3000;
    const SOCKET_RECONNECTION_DELAY_MAX = 15000;

    const toggleDarkMode = () => {
        setDarkMode((prevMode) => !prevMode);
    };

    const fetchSubscriptionStatus = async () => {
        try {
            if (user) {
                const idToken = await user.getIdToken();
                const response = await fetch('api/v1/subscriptions/status', {
                    method: 'GET',
                    headers: {
                        Authorization: `Bearer ${idToken}`,
                    },
                });

                if (!response.ok) {
                    throw new Error('Failed to fetch subscription status');
                }

                const data = await response.json();
                console.log(data)
                setSubscriptionStatus(data.subscription_status ?? null);
                setSubscriptionData(data); // Set the subscription data here

            }
        } catch (error) {
            console.error('Error fetching subscription status:', error);
            setSubscriptionStatus(null);
            setSubscriptionData(null); // Reset subscription data in case of error
        }
    };

    const registerUserInBackend = async (user: FirebaseUser) => {
        try {
            const idToken = await user.getIdToken();
            const response = await fetch(`/api/v1/users`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${idToken}`,
                },
            });

            if (!response.ok) {
                throw new Error(`Failed to register user: ${response.statusText}`);
            }

            console.log('User successfully registered in backend');
        } catch (error) {
            console.error('Error registering user in backend:', error);
        }
    };

    const disconnectWebSocket = () => {
        if (socket) {
            socket.close();
            setSocket(null);
        }
    };

    const initializeWebSocket = async () => {
        if (!user || socket) return;

        try {
            const token = await user.getIdToken();
            const newSocket = io({
                path: '/socket.io',
                extraHeaders: {
                    Authorization: `Bearer ${token}`
                },
                reconnection: true,
                reconnectionAttempts: SOCKET_RECONNECTION_ATTEMPTS,
                reconnectionDelay: SOCKET_RECONNECTION_DELAY,
                reconnectionDelayMax: SOCKET_RECONNECTION_DELAY_MAX,
                timeout: 60000,
                forceNew: true,
                transports: ['websocket']
            });

            newSocket.on('connect', () => {
                console.log('WebSocket connected');
            });

            newSocket.on('connect_error', (error) => {
                console.error('WebSocket connection error:', error);
            });

            newSocket.on('disconnect', (reason) => {
                console.log('WebSocket disconnected:', reason);
                if (reason === 'io server disconnect') {
                    newSocket.connect();
                }
            });

            // Add ping/pong for connection health check
            const pingInterval = setInterval(() => {
                if (newSocket.connected) {
                    newSocket.emit('ping');
                }
            }, 30000);

            newSocket.on('pong', () => {
                console.log('Received pong from server');
            });

            // Clean up on unmount
            newSocket.on('disconnect', () => {
                clearInterval(pingInterval);
            });

            setSocket(newSocket);

        } catch (error) {
            console.error('Error initializing WebSocket:', error);
        }
    };

    // UseEffect to call fetchSubscriptionStatus when the user is set
    useEffect(() => {
        if (!user) return;
        const registerAndFetchSubscription = async () => {
            if (user) {
                try {
                    await registerUserInBackend(user);  // Wait for the user registration to complete
                    await fetchSubscriptionStatus();    // Only fetch subscription status after registration
                } catch (error) {
                    console.error("Error in user registration or subscription status fetch:", error);
                }
            }
        };

        registerAndFetchSubscription(); // Call the async function

    }, [user]); // This effect runs when 'user' state changes

    // Initialize WebSocket when user is set
    useEffect(() => {
        if (user) {
            initializeWebSocket();
        } else {
            disconnectWebSocket();
        }
        return () => {
            disconnectWebSocket();
        };
    }, [user]);

    return (
        <UserContext.Provider
            value={{
                user,
                setUser, // Provide setter for user state
                darkMode,
                toggleDarkMode,
                setDarkMode,
                userSettings,
                setUserSettings,
                isPollingFiles,
                setIsPollingFiles,
                isPollingMessages,
                setIsPollingMessages,
                subscriptionStatus,
                setSubscriptionStatus,
                fetchSubscriptionStatus,
                subscriptionData,
                setSubscriptionData, // Provide setter for subscriptionData
                registerUserInBackend, // Provide registerUserInBackend method
                socket,
                initializeWebSocket,
                disconnectWebSocket,
            }}
        >
            {children}
        </UserContext.Provider>
    );
};

// Define useUserContext hook
export const useUserContext = (): UserContextType => {
    const context = useContext(UserContext);
    if (!context) {
        throw new Error('useUserContext must be used within a UserProvider');
    }
    return context;
};
