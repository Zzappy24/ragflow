package middleware

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"ragflow/internal/common"
	"ragflow/internal/dao"

	"github.com/gin-gonic/gin"
)

// These tests pre-seed the request-scoped RBAC context cache so we exercise
// the authorization decision tree (steps 4–8 of `RequirePermission`)
// without touching the database. The DB-backed resolver path is covered by
// the integration test added alongside the rollout, not here.
//
// Each test wires a small gin engine, sets `user_id` + `tenant_id` (which
// AuthMiddleware + WorkspaceMiddleware normally inject), pre-populates the
// rbacContextKey, and then asserts the middleware's decision.

func setupTestEngine(perm common.Permission) *gin.Engine {
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.GET("/x",
		// Seed the gin context the way AuthMiddleware + WorkspaceMiddleware
		// would normally do. Keeping the seeding inline (not a helper
		// middleware) makes each test self-contained.
		func(c *gin.Context) {
			c.Set("user_id", c.GetHeader("X-Test-User-ID"))
			c.Set(WorkspaceTenantKey, c.GetHeader("X-Test-Tenant-ID"))
			c.Next()
		},
		// Inject the pre-resolved RBAC context via the same key the
		// middleware checks first — bypasses the DAO call.
		func(c *gin.Context) {
			seedRBAC(c)
			c.Next()
		},
		RequirePermission(perm),
		func(c *gin.Context) { c.JSON(http.StatusOK, gin.H{"ok": true}) },
	)
	return r
}

func seedRBAC(c *gin.Context) {
	header := c.GetHeader("X-Test-RBAC-Role")
	if header == "nil" {
		// Simulate "user does not exist" — Python returns None from
		// _resolve_rbac_context. We model that with a nil pointer in the cache,
		// which loadOrResolveContext will not return cleanly, so use absent
		// key instead by NOT setting it; loadOrResolveContext would then call
		// the DAO. For unit tests where we want to skip the DAO, we seed an
		// explicit non-nil zero-context with empty workspace_id — that path
		// hits the "no workspace" branch and denies correctly.
		c.Set(rbacContextKey, (*dao.RBACContext)(nil))
		return
	}
	ctx := &dao.RBACContext{
		WorkspaceID: c.GetHeader("X-Test-Workspace-ID"),
	}
	if header == "superuser" {
		ctx.IsSuperuser = true
	} else if header == "org_admin" {
		ctx.OrgRole = common.OrgRoleOrgAdmin
	} else if header != "" {
		ctx.WsRole = common.WsRole(header)
	}
	c.Set(rbacContextKey, ctx)
}

func do(r *gin.Engine, headers map[string]string) *httptest.ResponseRecorder {
	req := httptest.NewRequest("GET", "/x", nil)
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	return w
}

func TestRequirePermission_MissingAuth(t *testing.T) {
	r := setupTestEngine(common.PermDatasetRead)
	// no user_id → 403
	w := do(r, map[string]string{
		"X-Test-Tenant-ID":  "t-1",
		"X-Test-RBAC-Role":  "viewer",
		"X-Test-Workspace-ID": "ws-1",
	})
	if w.Code != http.StatusForbidden {
		t.Fatalf("missing user_id: got %d, want 403", w.Code)
	}
}

func TestRequirePermission_MissingTenant(t *testing.T) {
	r := setupTestEngine(common.PermDatasetRead)
	w := do(r, map[string]string{
		"X-Test-User-ID":   "u-1",
		"X-Test-RBAC-Role": "viewer",
	})
	if w.Code != http.StatusForbidden {
		t.Fatalf("missing tenant_id: got %d, want 403", w.Code)
	}
}

func TestRequirePermission_SuperuserBypass(t *testing.T) {
	// is_superuser → ALLOW (even with no workspace membership)
	r := setupTestEngine(common.PermLLMConfigure)
	w := do(r, map[string]string{
		"X-Test-User-ID":     "u-super",
		"X-Test-Tenant-ID":   "t-1",
		"X-Test-RBAC-Role":   "superuser",
		"X-Test-Workspace-ID": "ws-1",
	})
	if w.Code != http.StatusOK {
		t.Fatalf("superuser bypass: got %d, want 200", w.Code)
	}
}

func TestRequirePermission_OrgAdminBypass(t *testing.T) {
	// org_admin of workspace's org → ALLOW (any permission)
	r := setupTestEngine(common.PermAPIKeyManage)
	w := do(r, map[string]string{
		"X-Test-User-ID":     "u-orgadm",
		"X-Test-Tenant-ID":   "t-1",
		"X-Test-RBAC-Role":   "org_admin",
		"X-Test-Workspace-ID": "ws-1",
	})
	if w.Code != http.StatusOK {
		t.Fatalf("org_admin bypass: got %d, want 200", w.Code)
	}
}

func TestRequirePermission_NoWorkspace(t *testing.T) {
	// User exists but workspace_id is empty (no workspace for tenant_id) → 403
	r := setupTestEngine(common.PermDatasetRead)
	w := do(r, map[string]string{
		"X-Test-User-ID":   "u-1",
		"X-Test-Tenant-ID": "t-1",
		"X-Test-RBAC-Role": "viewer",
		// no X-Test-Workspace-ID → seeded ctx has empty WorkspaceID
	})
	if w.Code != http.StatusForbidden {
		t.Fatalf("no workspace: got %d, want 403", w.Code)
	}
}

func TestRequirePermission_ViewerGrantedRead(t *testing.T) {
	r := setupTestEngine(common.PermDatasetRead)
	w := do(r, map[string]string{
		"X-Test-User-ID":     "u-1",
		"X-Test-Tenant-ID":   "t-1",
		"X-Test-RBAC-Role":   "viewer",
		"X-Test-Workspace-ID": "ws-1",
	})
	if w.Code != http.StatusOK {
		t.Fatalf("viewer reading dataset: got %d, want 200", w.Code)
	}
}

func TestRequirePermission_ViewerDeniedCreate(t *testing.T) {
	r := setupTestEngine(common.PermDatasetCreate)
	w := do(r, map[string]string{
		"X-Test-User-ID":     "u-1",
		"X-Test-Tenant-ID":   "t-1",
		"X-Test-RBAC-Role":   "viewer",
		"X-Test-Workspace-ID": "ws-1",
	})
	if w.Code != http.StatusForbidden {
		t.Fatalf("viewer creating dataset: got %d, want 403", w.Code)
	}
}

func TestRequirePermission_EditorDeniedLLMConfigure(t *testing.T) {
	// Editor has CRUD on data but NOT admin permissions.
	r := setupTestEngine(common.PermLLMConfigure)
	w := do(r, map[string]string{
		"X-Test-User-ID":     "u-1",
		"X-Test-Tenant-ID":   "t-1",
		"X-Test-RBAC-Role":   "editor",
		"X-Test-Workspace-ID": "ws-1",
	})
	if w.Code != http.StatusForbidden {
		t.Fatalf("editor configuring LLM: got %d, want 403", w.Code)
	}
}

func TestRequirePermission_WsAdminGrantedAll(t *testing.T) {
	for _, perm := range []common.Permission{
		common.PermDatasetCreate, common.PermLLMConfigure,
		common.PermAPIKeyManage, common.PermAuditRead, common.PermGroupManage,
	} {
		t.Run(string(perm), func(t *testing.T) {
			r := setupTestEngine(perm)
			w := do(r, map[string]string{
				"X-Test-User-ID":     "u-1",
				"X-Test-Tenant-ID":   "t-1",
				"X-Test-RBAC-Role":   "ws_admin",
				"X-Test-Workspace-ID": "ws-1",
			})
			if w.Code != http.StatusOK {
				t.Fatalf("ws_admin %s: got %d, want 200", perm, w.Code)
			}
		})
	}
}
