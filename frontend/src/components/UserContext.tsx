import { createContext, useContext, useState, ReactNode } from 'react';
import { User } from './types';

interface UserContextProps {
    children: ReactNode;
}

interface UserContextValue {
    user: User | null;
    updateUser: (newUser: User | null) => void;
}

const UserContext = createContext<UserContextValue | undefined>(undefined);

export const UserProvider: React.FC<UserContextProps> = ({ children }) => {
    const [user, setUser] = useState<User | null>(null);

    const updateUser = (newUser: User | null) => {
        setUser(newUser);
    };

    return (
        <UserContext.Provider value={{ user, updateUser }}>
            {children}
        </UserContext.Provider>
    );
};

export const useUser = () => {
    const context = useContext(UserContext);
    if (!context) {
        throw new Error('useUser must be used within a UserProvider');
    }
    return context;
};
