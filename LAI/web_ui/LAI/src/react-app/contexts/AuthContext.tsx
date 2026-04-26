import {
  createContext,
  useContext,
  useState,
  ReactNode,
  useEffect,
} from "react";
import {
  generateToken,
  verifyToken,
  storeToken,
  removeToken,
  getToken,
} from "@/react-app/utils/jwt";

interface User {
  id: string;
  email: string;
  fullName: string;
}

interface AuthContextType {
  user: User | null;
  isAuthenticated: boolean;
  login: (email: string, password: string) => Promise<void>;
  signup: (fullName: string, email: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    // Check if user is already logged in (from JWT token in localStorage)
    const token = getToken();
    if (token) {
      const decoded = verifyToken(token);
      if (decoded) {
        setUser({
          id: decoded.id,
          email: decoded.email,
          fullName: decoded.fullName,
        });
      } else {
        // Token expired or invalid, remove it
        removeToken();
      }
    }
    setIsLoading(false);
  }, []);

  const login = async (email: string, password: string) => {
    // Demo implementation - accepts any credentials
    // In production, this would call your backend API with the credentials
    // and the backend would return a JWT token
    if (!email || !password) {
      throw new Error("Email and password are required");
    }

    // Simulate API call delay
    await new Promise((resolve) => setTimeout(resolve, 500));

    const newUser: User = {
      id: Date.now().toString(),
      email,
      fullName: email.split("@")[0],
    };

    // Generate JWT token
    const token = generateToken({
      id: newUser.id,
      email: newUser.email,
      fullName: newUser.fullName,
    });

    // Store token in localStorage
    storeToken(token);
    setUser(newUser);
  };

  const signup = async (fullName: string, email: string, password: string) => {
    // Demo implementation
    // In production, this would call your backend API
    if (!fullName || !email || !password) {
      throw new Error("All fields are required");
    }

    // Simulate API call delay
    await new Promise((resolve) => setTimeout(resolve, 500));

    const newUser: User = {
      id: Date.now().toString(),
      email,
      fullName,
    };

    // Generate JWT token
    const token = generateToken({
      id: newUser.id,
      email: newUser.email,
      fullName: newUser.fullName,
    });

    // Store token in localStorage
    storeToken(token);
    setUser(newUser);
  };

  const logout = () => {
    setUser(null);
    removeToken();
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        Loading...
      </div>
    );
  }

  return (
    <AuthContext.Provider
      value={{ user, isAuthenticated: !!user, login, signup, logout }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}
