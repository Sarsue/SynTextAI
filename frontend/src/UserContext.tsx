import React, { createContext, useContext, useState, ReactNode, useEffect } from 'react';
import { User as FirebaseUser } from 'firebase/auth';

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
    user: FirebaseUser | null;
    setUser: (user: FirebaseUser | null) => void;
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
    socket: WebSocket | null;
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
    const [user, setUser] = useState<FirebaseUser | null>(null);
    const [darkMode, setDarkMode] = useState<boolean>(false);
    const [userSettings, setUserSettings] = useState<UserSettings>({
        comprehensionLevel: 'Beginner',
        selectedLanguage: 'English',
    });
    const [isPollingFiles, setIsPollingFiles] = useState<boolean>(false);
    const [isPollingMessages, setIsPollingMessages] = useState<boolean>(false);
    const [subscriptionStatus, setSubscriptionStatus] = useState<string | null>(null);
    const [subscriptionData, setSubscriptionData] = useState<SubscriptionData | null>(null);
    const [socket, setWebsocket] = useState<WebSocket | null>(null);

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
                console.log(data);
                setSubscriptionStatus(data.subscription_status ?? null);
                setSubscriptionData(data);
            }
        } catch (error) {
            console.error('Error fetching subscription status:', error);
            setSubscriptionStatus(null);
            setSubscriptionData(null);
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
            setWebsocket(null);
        }
    };

    const initializeWebSocket = async () => {
        if (!user || socket) return;

        try {
            const token = await user.getIdToken();
            const wsUrl = `ws://${window.location.host}/ws/${user.uid}`;
            const ws = new WebSocket(wsUrl);

            ws.onopen = () => {
                console.log('WebSocket connected');
                // Send the authentication token after connection
                ws.send(JSON.stringify({ type: 'auth', token }));
            };

            ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                console.log('WebSocket message received:', data);

                // Handle specific events
                if (data.event === 'file_processed') {
                    if (data.status === 'success') {
                        console.log(`File ${data.result.filename} processed successfully`);
                    } else {
                        console.error(`Error processing file ${data.result.filename}: ${data.error}`);
                    }
                } else if (data.event === 'message_received') {
                    if (data.status === 'error') {
                        console.error(`Error: ${data.error}`);
                    } else {
                        console.log('Message received:', data.message);
                    }
                }
            };

            ws.onerror = (error) => {
                console.error('WebSocket error:', error);
            };

            ws.onclose = () => {
                console.log('WebSocket disconnected');
                // Attempt to reconnect after a delay
                setTimeout(() => initializeWebSocket(), 3000);
            };

            setWebsocket(ws);
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
                    await registerUserInBackend(user);
                    await fetchSubscriptionStatus();
                } catch (error) {
                    console.error("Error in user registration or subscription status fetch:", error);
                }
            }
        };

        registerAndFetchSubscription();
    }, [user]);

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
                setUser,
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
                setSubscriptionData,
                registerUserInBackend,
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