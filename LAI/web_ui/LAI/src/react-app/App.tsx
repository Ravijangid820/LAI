import { BrowserRouter as Router, Routes, Route } from "react-router";
import { ThemeProvider } from "@/react-app/contexts/ThemeContext";

import { AuthProvider } from "@/react-app/contexts/AuthContext";
import { ProtectedRoute } from "@/react-app/components/ProtectedRoute";
import LandingPage from "@/react-app/pages/Landing";
import LoginPage from "@/react-app/pages/Login";
import SignupPage from "@/react-app/pages/Signup";
import DashboardLayout from "@/react-app/components/DashboardLayout";
import DashboardPage from "@/react-app/pages/Dashboard";
import DashboardChatPage from "@/react-app/pages/DashboardChat";
import DashboardDocumentsPage from "@/react-app/pages/DashboardDocuments";
import DashboardProjectsPage from "@/react-app/pages/DashboardProjects";
import DashboardRiskPage from "@/react-app/pages/DashboardRisk";
import DashboardSettingsPage from "@/react-app/pages/DashboardSettings";

export default function App() {
  return (
    <ThemeProvider>
      <AuthProvider>
        <Router>
          <Routes>
            <Route path="/" element={<LandingPage />} />
            <Route path="/login" element={<LoginPage />} />
            <Route path="/signup" element={<SignupPage />} />
            <Route
              path="/dashboard"
              element={
                <ProtectedRoute>
                  <DashboardLayout />
                </ProtectedRoute>
              }
            >
              <Route index element={<DashboardPage />} />
              <Route path="chat" element={<DashboardChatPage />} />
              <Route path="documents" element={<DashboardDocumentsPage />} />
              <Route path="projects" element={<DashboardProjectsPage />} />
              <Route path="risk" element={<DashboardRiskPage />} />
              <Route path="settings" element={<DashboardSettingsPage />} />
            </Route>
          </Routes>
        </Router>
      </AuthProvider>
    </ThemeProvider>
  );
}
