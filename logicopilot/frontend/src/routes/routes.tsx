import { Navigate, Route, Routes } from "react-router-dom";
import { ProtectedRoute } from "../auth/ProtectedRoute";
import { LoginPage } from "../pages/LoginPage";
import { NotAuthorizedPage } from "../pages/NotAuthorizedPage";
import { SuperAdminDashboard } from "../pages/super-admin/DashboardShell";
import { TemplateCreationWizard } from "../pages/super-admin/template-wizard/TemplateCreationWizard";
import { TemplateListPage } from "../pages/super-admin/TemplateListPage";
import { ErpScriptsPage } from "../pages/super-admin/ErpScriptsPage";
import { ErpScriptRecorderPage } from "../pages/super-admin/ErpScriptRecorderPage";
import { ErpEntryBrowserPage } from "../pages/super-admin/ErpEntryBrowserPage";
import { TenantAdminDashboard } from "../pages/tenant-admin/DashboardShell";
import { MastersPage } from "../pages/tenant-admin/MastersPage";
import { UsersPage } from "../pages/tenant-admin/UsersPage";
import { OperatorDashboard } from "../pages/operator/DashboardShell";
import { ManagerDashboard } from "../pages/manager/DashboardShell";
import { JobsPage } from "../pages/jobs/JobsPage";
import { JobRunPage } from "../pages/jobs/JobRunPage";
import { EDocketPage } from "../pages/jobs/EDocketPage";
import { IrnPendingListPage } from "../pages/irn/IrnPendingListPage";
import { IrnPendingDetailPage } from "../pages/irn/IrnPendingDetailPage";
import { CustomerListPage } from "../pages/tenant-admin/CustomerListPage";
import { DataTransformationPage } from "../pages/super-admin/DataTransformationPage";
import { DataTransformationDetailPage } from "../pages/super-admin/DataTransformationDetailPage";
import { PendingMailPage } from "../pages/super-admin/PendingMailPage";
import { SettingsPage } from "../pages/super-admin/SettingsPage";
import { FilterPagesPage } from "../pages/super-admin/FilterPagesPage";
import { SpendAnalyticsPage } from "../pages/super-admin/SpendAnalyticsPage";

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/not-authorized" element={<NotAuthorizedPage />} />
      <Route
        path="/super-admin"
        element={
          <ProtectedRoute allowedRoles={["super_admin"]}>
            <SuperAdminDashboard />
          </ProtectedRoute>
        }
      />
      <Route
        path="/super-admin/template-creation"
        element={
          <ProtectedRoute allowedRoles={["super_admin"]}>
            <TemplateCreationWizard />
          </ProtectedRoute>
        }
      />
      <Route
        path="/super-admin/template-list"
        element={
          <ProtectedRoute allowedRoles={["super_admin"]}>
            <TemplateListPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/super-admin/erp-scripts"
        element={
          <ProtectedRoute allowedRoles={["super_admin"]}>
            <ErpScriptsPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/super-admin/erp-scripts/:scriptId"
        element={
          <ProtectedRoute allowedRoles={["super_admin"]}>
            <ErpScriptRecorderPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/super-admin/entry-browser"
        element={
          <ProtectedRoute allowedRoles={["super_admin"]}>
            <ErpEntryBrowserPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/super-admin/pending-mail"
        element={
          <ProtectedRoute allowedRoles={["super_admin"]}>
            <PendingMailPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/super-admin/settings"
        element={
          <ProtectedRoute allowedRoles={["super_admin"]}>
            <SettingsPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/super-admin/filter-pages"
        element={
          <ProtectedRoute allowedRoles={["super_admin"]}>
            <FilterPagesPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/super-admin/spend-analytics"
        element={
          <ProtectedRoute allowedRoles={["super_admin"]}>
            <SpendAnalyticsPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/tenant-admin"
        element={
          <ProtectedRoute allowedRoles={["tenant_admin"]}>
            <TenantAdminDashboard />
          </ProtectedRoute>
        }
      />
      <Route
        path="/tenant-admin/customers"
        element={
          <ProtectedRoute allowedRoles={["tenant_admin"]}>
            <CustomerListPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/tenant-admin/masters"
        element={
          <ProtectedRoute allowedRoles={["tenant_admin"]}>
            <MastersPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/tenant-admin/users"
        element={
          <ProtectedRoute allowedRoles={["tenant_admin"]}>
            <UsersPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/super-admin/data-transformation"
        element={
          <ProtectedRoute allowedRoles={["super_admin"]}>
            <DataTransformationPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/super-admin/data-transformation/:groupId"
        element={
          <ProtectedRoute allowedRoles={["super_admin"]}>
            <DataTransformationDetailPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/operator"
        element={
          <ProtectedRoute allowedRoles={["operator"]}>
            <OperatorDashboard />
          </ProtectedRoute>
        }
      />
      <Route
        path="/manager"
        element={
          <ProtectedRoute allowedRoles={["manager"]}>
            <ManagerDashboard />
          </ProtectedRoute>
        }
      />
      <Route
        path="/jobs"
        element={
          <ProtectedRoute allowedRoles={["operator", "super_admin", "admin", "gk2", "manager"]}>
            <JobsPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/jobs/:jobId"
        element={
          <ProtectedRoute allowedRoles={["operator", "super_admin", "admin", "gk2", "manager"]}>
            <JobRunPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/jobs/:jobId/e-docket"
        element={
          <ProtectedRoute allowedRoles={["operator", "super_admin", "admin", "gk2", "manager"]}>
            <EDocketPage />
          </ProtectedRoute>
        }
      />
      {/* Standalone and deliberately UNPROTECTED - no login screen. Reachable only by pasting
          this exact link (it must carry ?key=...) - see app/api/v1/public_irn.py on the
          backend for what actually gates access. Never linked from the normal job screens or
          any sidebar navigation. */}
      <Route path="/irn-pending" element={<IrnPendingListPage />} />
      <Route path="/irn-pending/:jobId" element={<IrnPendingDetailPage />} />
      <Route path="/" element={<Navigate to="/login" replace />} />
      <Route path="*" element={<Navigate to="/login" replace />} />
    </Routes>
  );
}
