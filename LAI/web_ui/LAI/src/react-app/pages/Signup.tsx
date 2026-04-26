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
  PersonIcon,
  BuildingIcon,
  ArrowRightIcon,
} from "@/react-app/components/icons";


export default function SignupPage() {
  const navigate = useNavigate();
  const { signup } = useAuth();

  const [showPassword, setShowPassword] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");
  const [formData, setFormData] = useState({
    fullName: "",
    email: "",
    company: "",
    password: "",
    confirmPassword: "",
    agreeTerms: false,
  });

  const handleChange = (field: string, value: string | boolean) =>
    setFormData((prev) => ({ ...prev, [field]: value }));

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");

    // Validation
    if (formData.password !== formData.confirmPassword) {
      setError("Passwords do not match.");
      return;
    }

    if (strength.score < 3) {
      setError("Please choose a stronger password.");
      return;
    }

    setIsLoading(true);
    try {
      await signup(formData.fullName, formData.email, formData.password);
      navigate("/dashboard");
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Signup failed. Please try again.",
      );
    } finally {
      setIsLoading(false);
    }
  };

  const passwordStrength = () => {
    const { password } = formData;
    if (!password.length) return { score: 0, label: "", color: "" };
    if (password.length < 6)
      return { score: 1, label: "Weak", color: "bg-red-500" };
    if (password.length < 10)
      return { score: 2, label: "Fair", color: "bg-yellow-500" };
    if (password.match(/^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)/))
      return { score: 4, label: "Strong", color: "bg-blue-500" };
    return { score: 3, label: "Good", color: "bg-indigo-500" };
  };

  const strength = passwordStrength();



  return (
    <div className="min-h-screen bg-slate-50 dark:bg-slate-950 flex flex-col items-center justify-center p-6 relative overflow-hidden">
      <div className="fixed top-6 right-6 z-50">
        <ThemeToggle />
      </div>
      {/* Subtle Background Pattern */}
      <div className="absolute inset-0 bg-[linear-gradient(to_right,#80808012_1px,transparent_1px),linear-gradient(to_bottom,#80808012_1px,transparent_1px)] bg-[size:40px_40px] pointer-events-none" />

      <div className="w-full max-w-xl relative z-10 space-y-8">
        <div className="text-center space-y-6">
          <Link to="/" className="inline-block">
            <Logo size="lg" />
          </Link>
          <div className="space-y-2">
            <h1 className="text-2xl font-semibold tracking-tight">Create your account</h1>
            <p className="text-muted-foreground text-sm">
              Join 500+ legal professionals using AI-powered due diligence
            </p>
          </div>
        </div>

        <div className="bg-card border border-border/50 shadow-sm p-8 rounded-md space-y-6">
          {error && (
            <div className="p-3 rounded-md bg-red-500/10 border border-red-500/20 text-red-600 text-sm">
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-6">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {/* Full Name */}
              <div className="space-y-2">
                <Label htmlFor="fullName" className="font-medium text-xs uppercase tracking-wider text-muted-foreground">
                  Full Name
                </Label>
                <div className="relative">
                  <PersonIcon className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                  <Input
                    id="fullName"
                    type="text"
                    placeholder="John Doe"
                    value={formData.fullName}
                    onChange={(e) => handleChange("fullName", e.target.value)}
                    className="pl-11 h-11 rounded-md bg-background border-input focus-visible:ring-1 focus-visible:ring-primary shadow-sm"
                    required
                  />
                </div>
              </div>

              {/* Company */}
              <div className="space-y-2">
                <Label htmlFor="company" className="font-medium text-xs uppercase tracking-wider text-muted-foreground">
                  Company
                </Label>
                <div className="relative">
                  <BuildingIcon className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                  <Input
                    id="company"
                    type="text"
                    placeholder="Acme Inc."
                    value={formData.company}
                    onChange={(e) => handleChange("company", e.target.value)}
                    className="pl-11 h-11 rounded-md bg-background border-input focus-visible:ring-1 focus-visible:ring-primary shadow-sm"
                    required
                  />
                </div>
              </div>

              {/* Email */}
              <div className="md:col-span-2 space-y-2">
                <Label htmlFor="email" className="font-medium text-xs uppercase tracking-wider text-muted-foreground">
                  Work Email
                </Label>
                <div className="relative">
                  <EnvelopeIcon className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                  <Input
                    id="email"
                    type="email"
                    placeholder="name@company.com"
                    value={formData.email}
                    onChange={(e) => handleChange("email", e.target.value)}
                    className="pl-11 h-11 rounded-md bg-background border-input focus-visible:ring-1 focus-visible:ring-primary shadow-sm"
                    required
                  />
                </div>
              </div>

              {/* Password */}
              <div className="space-y-2">
                <Label htmlFor="password" className="font-medium text-xs uppercase tracking-wider text-muted-foreground">
                  Password
                </Label>
                <div className="relative">
                  <PadlockIcon className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                  <Input
                    id="password"
                    type={showPassword ? "text" : "password"}
                    placeholder="Password"
                    value={formData.password}
                    onChange={(e) => handleChange("password", e.target.value)}
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
                {formData.password && (
                  <div className="space-y-2">
                    <div className="flex gap-1">
                      {[1, 2, 3, 4].map((i) => (
                        <div
                          key={i}
                          className={`h-1 flex-1 rounded-full transition-colors ${i <= strength.score ? strength.color : "bg-muted"}`}
                        />
                      ))}
                    </div>
                    <p className="text-[10px] text-muted-foreground uppercase tracking-tight">
                      Strength: <span className="font-semibold">{strength.label}</span>
                    </p>
                  </div>
                )}
              </div>

              {/* Confirm Password */}
              <div className="space-y-2">
                <Label htmlFor="confirmPassword" className="font-medium text-xs uppercase tracking-wider text-muted-foreground">
                  Confirm Password
                </Label>
                <div className="relative">
                  <PadlockIcon className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                  <Input
                    id="confirmPassword"
                    type={showPassword ? "text" : "password"}
                    placeholder="Confirm Password"
                    value={formData.confirmPassword}
                    onChange={(e) => handleChange("confirmPassword", e.target.value)}
                    className="pl-11 pr-11 h-11 rounded-md bg-background border-input focus-visible:ring-1 focus-visible:ring-primary shadow-sm"
                    required
                  />
                </div>
                {formData.confirmPassword && formData.password !== formData.confirmPassword && (
                  <p className="text-[10px] text-red-500 uppercase tracking-tight font-semibold">
                    Passwords do not match
                  </p>
                )}
              </div>
            </div>

            <div className="flex items-start gap-2 pt-1 border-t border-border/50 pt-6">
              <Checkbox
                id="terms"
                checked={formData.agreeTerms}
                onCheckedChange={(c: boolean | "indeterminate") =>
                  handleChange("agreeTerms", c === true)
                }
                className="mt-0.5"
                required
              />
              <Label
                htmlFor="terms"
                className="text-xs font-normal text-muted-foreground cursor-pointer leading-normal"
              >
                By creating an account, I agree to the <a href="#" className="text-primary hover:underline">Terms of Service</a> and <a href="#" className="text-primary hover:underline">Privacy Policy</a>.
              </Label>
            </div>

            <Button
              type="submit"
              className="w-full h-11 text-sm font-semibold rounded-md shadow-sm bg-primary hover:bg-primary/90 text-primary-foreground transition-all"
              disabled={!formData.agreeTerms || isLoading}
            >
              {isLoading ? "Creating account..." : "Get Started"}
              {!isLoading && <ArrowRightIcon className="ml-2 w-4 h-4" />}
            </Button>
          </form>
        </div>

        <p className="text-center text-sm text-muted-foreground">
          Already have an account?{" "}
          <Link
            to="/login"
            className="text-primary hover:underline font-medium"
          >
            Sign in
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
