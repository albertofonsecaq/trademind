import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider } from "./context/AuthContext";
import { ProtectedRoute } from "./components/ProtectedRoute";
import { Login } from "./pages/Login";
import { Register } from "./pages/Register";
import { Dashboard } from "./pages/Dashboard";
import { Ask } from "./pages/Ask";
import { AdminSources } from "./pages/AdminSources";
import { TelegramConnect } from "./pages/TelegramConnect";
import { StrategyLibrary } from "./pages/StrategyLibrary";
import { MyPlan } from "./pages/MyPlan";
import { Changelog } from "./pages/Changelog";
import { StrategyCardDetail } from "./pages/StrategyCardDetail";
import { PaperTrading } from "./pages/PaperTrading";
import { Billing } from "./pages/Billing";
import { SuperAdmin } from "./pages/SuperAdmin";
import { Help } from "./pages/Help";

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/register" element={<Register />} />
          <Route path="/" element={<ProtectedRoute><Dashboard /></ProtectedRoute>} />
          <Route path="/ask" element={<ProtectedRoute><Ask /></ProtectedRoute>} />
          <Route path="/library" element={<ProtectedRoute><StrategyLibrary /></ProtectedRoute>} />
          <Route path="/library/:cardId" element={<ProtectedRoute><StrategyCardDetail /></ProtectedRoute>} />
          <Route path="/plan" element={<ProtectedRoute><MyPlan /></ProtectedRoute>} />
          <Route path="/trading" element={<ProtectedRoute><PaperTrading /></ProtectedRoute>} />
          <Route path="/billing" element={<ProtectedRoute><Billing /></ProtectedRoute>} />
          <Route path="/admin" element={<ProtectedRoute><SuperAdmin /></ProtectedRoute>} />
          <Route path="/help" element={<ProtectedRoute><Help /></ProtectedRoute>} />
          <Route path="/changelog" element={<ProtectedRoute><Changelog /></ProtectedRoute>} />
          <Route path="/sources" element={<ProtectedRoute><AdminSources /></ProtectedRoute>} />
          <Route path="/sources/connect-telegram/:workspaceId" element={<ProtectedRoute><TelegramConnect /></ProtectedRoute>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}
