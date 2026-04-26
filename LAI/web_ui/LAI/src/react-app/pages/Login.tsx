import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { Button } from "@/react-app/components/ui/button";
import { Input } from "@/react-app/components/ui/input";
import { Label } from "@/react-app/components/ui/label";
import { Checkbox } from "@/react-app/components/ui/checkbox";
import { Logo } from "@/react-app/components/Logo";
import { ThemeToggle } from "@/react-app/components/ThemeToggle";
import { useAuth } from "@/react-app/contexts/AuthContext";
import {
  LensIcon,
  LensOffIcon,
  EnvelopeIcon,
  PadlockIcon,
  ArrowRightIcon,
} from "@/react-app/components/icons";

export default function LoginPage() {
  const navigate = useNavigate();
  const { login } = useAuth();

  const [showPassword, setShowPassword] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [rememberMe, setRememberMe] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setIsLoading(true);
    try {
      await login(email, password);
      navigate("/dashboard");
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Login failed. Please try again.",
      );
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-50 dark:bg-slate-950 flex flex-col items-center justify-center p-6 relative overflow-hidden">
      <div className="fixed top-6 right-6 z-50">
        <ThemeToggle />
      </div>
      {/* Subtle Background Pattern */}
      <div className="absolute inset-0 bg-[linear-gradient(to_right,#80808012_1px,transparent_1px),linear-gradient(to_bottom,#80808012_1px,transparent_1px)] bg-[size:40px_40px] pointer-events-none" />

      <div className="w-full max-w-md relative z-10 space-y-8">
        <div className="text-center space-y-6">
          <Link to="/" className="inline-block">
            <Logo size="lg" />
          </Link>
          <div className="space-y-2">
            <h1 className="text-2xl font-semibold tracking-tight">Welcome back</h1>
            <p className="text-muted-foreground text-sm">
              Secures access to your legal AI dashboard
            </p>
          </div>
        </div>

        <div className="bg-card border border-border/50 shadow-sm p-8 rounded-md space-y-6">
          {error && (
            <div className="p-3 rounded-md bg-red-500/10 border border-red-500/20 text-red-600 text-sm">
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-5">
            <div className="space-y-4">
              {/* Email */}
              <div className="space-y-2">
                <Label htmlFor="email" className="font-medium text-xs uppercase tracking-wider text-muted-foreground">
                  Email Address
                </Label>
                <div className="relative">
                  <EnvelopeIcon className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                  <Input
                    id="email"
                    type="email"
                    placeholder="name@firm.com"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    className="pl-11 h-11 rounded-md bg-background border-input focus-visible:ring-1 focus-visible:ring-primary shadow-sm"
                    required
                  />
                </div>
              </div>

              {/* Password */}
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <Label htmlFor="password" className="font-medium text-xs uppercase tracking-wider text-muted-foreground">
                    Password
                  </Label>
                  <Link
                    to="/forgot-password"
                    className="text-xs text-muted-foreground hover:text-foreground transition-colors"
                  >
                    Forgot password?
                  </Link>
                </div>
                <div className="relative">
                  <PadlockIcon className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                  <Input
                    id="password"
                    type={showPassword ? "text" : "password"}
                    placeholder="Enter your password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    className="pl-11 pr-11 h-11 rounded-md bg-background border-input focus-visible:ring-1 focus-visible:ring-primary shadow-sm"
                    required
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword(!showPassword)}
                    className="absolute right-4 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
                  >
                    {showPassword ? (
                      <LensOffIcon className="w-4 h-4" />
                    ) : (
                      <LensIcon className="w-4 h-4" />
                    )}
                  </button>
                </div>
              </div>
            </div>

            <div className="flex items-center gap-2 pt-1">
              <Checkbox
                id="remember"
                checked={rememberMe}
                onCheckedChange={(c: boolean | "indeterminate") => setRememberMe(c === true)}
              />
              <Label
                htmlFor="remember"
                className="text-sm font-normal text-muted-foreground cursor-pointer"
              >
                Keep me signed in
              </Label>
            </div>

            <Button
              type="submit"
              className="w-full h-11 text-sm font-semibold rounded-md shadow-sm bg-primary hover:bg-primary/90 text-primary-foreground transition-all"
              disabled={isLoading}
            >
              {isLoading ? "Verifying..." : "Sign in"}
              {!isLoading && <ArrowRightIcon className="ml-2 w-4 h-4" />}
            </Button>
          </form>
        </div>

        <p className="text-center text-sm text-muted-foreground">
          Don't have an account?{" "}
          <Link
            to="/signup"
            className="text-primary hover:underline font-medium"
          >
            Create account
          </Link>
        </p>
      </div>

      <div className="absolute bottom-8 left-1/2 -translate-x-1/2 flex items-center gap-6 opacity-60">
        <span className="text-xs text-muted-foreground font-medium uppercase tracking-widest">
          &copy; 2026 Legal AI
        </span>
      </div>
    </div>
  );
}
