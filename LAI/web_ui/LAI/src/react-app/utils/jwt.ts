// Simple JWT utility for client-side token handling
// Note: In production, tokens should be generated on the backend
// The VITE_JWT_SECRET (from .env) should be used by your backend for HMAC signing

interface JWTPayload {
  id: string;
  email: string;
  fullName: string;
  iat: number; // issued at
  exp: number; // expiration
}

const TOKEN_EXPIRY = 24 * 60 * 60 * 1000; // 24 hours

// Convert string to base64
function toBase64(str: string): string {
  return btoa(unescape(encodeURIComponent(str)));
}

// Convert base64 to string
function fromBase64(str: string): string {
  return decodeURIComponent(escape(atob(str)));
}

// Simulate JWT signing (in production, backend would do this)
export function generateToken(payload: Omit<JWTPayload, 'iat' | 'exp'>): string {
  const now = Date.now();
  const token = {
    ...payload,
    iat: Math.floor(now / 1000),
    exp: Math.floor((now + TOKEN_EXPIRY) / 1000),
  };

  // Encode as base64 (in production, use proper JWT with HMAC)
  return `tokens_${toBase64(JSON.stringify(token))}`;
}

// Verify and decode token
export function verifyToken(token: string): JWTPayload | null {
  try {
    if (!token.startsWith('tokens_')) {
      return null;
    }

    const decoded = JSON.parse(
      fromBase64(token.slice(7))
    ) as JWTPayload;

    // Check expiration
    const now = Math.floor(Date.now() / 1000);
    if (decoded.exp < now) {
      return null;
    }

    return decoded;
  } catch (error) {
    console.error('Token verification failed:', error);
    return null;
  }
}

// Store token in localStorage
export function storeToken(token: string): void {
  localStorage.setItem('lai-auth-token', token);
}

// Retrieve token from localStorage
export function getToken(): string | null {
  return localStorage.getItem('lai-auth-token');
}

// Remove token from localStorage
export function removeToken(): void {
  localStorage.removeItem('lai-auth-token');
}

// Get user from token
export function getUserFromToken(): JWTPayload | null {
  const token = getToken();
  if (!token) {
    return null;
  }
  return verifyToken(token);
}

