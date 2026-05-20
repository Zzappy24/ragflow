// Package middleware — RBAC middleware for the RAGFlow Go server.
//
// CUSTOM B2B SaaS — mirrors `@require_permission(Permission.X)` from
// `api/apps/extensions/rbac.py`. Sits AFTER AuthMiddleware (which sets
// `user_id`) and WorkspaceMiddleware (which sets `tenant_id`).
//
// Authorization decision tree (must stay in sync with `has_permission` in
// rbac.py):
//
//  1. user_id missing      → 403 (defense in depth; auth middleware should have caught)
//  2. tenant_id missing    → 403 (workspace middleware should have caught)
//  3. RBAC context unknown → 403 (user does not exist or DB error)
//  4. is_superuser         → ALLOW (TODO: audit log SUPERUSER_BYPASS, port AuditService)
//  5. no workspace for tenant → 403
//  6. org_admin of workspace's org → ALLOW
//  7. ws_role grants the permission per the matrix → ALLOW
//  8. otherwise → 403
//
// The resolved RBACContext is cached on the gin.Context for the lifetime of
// the request — matches Python's `g._rbac_ctx` request-scoped cache so a
// single request can RequirePermission() multiple permissions without
// hitting the DB more than once.
package middleware

import (
	"net/http"

	"ragflow/internal/common"
	"ragflow/internal/dao"

	"github.com/gin-gonic/gin"
	"go.uber.org/zap"
)

// rbacContextKey is the gin.Context key for the cached RBACContext.
const rbacContextKey = "_rbac_ctx"

// RequirePermission returns a gin middleware that allows the request only
// if the caller's ws_role (or org_admin/superuser bypass) grants `perm` on
// the active workspace tenant. On denial it writes a 403 JSON body and
// aborts the handler chain.
//
// Usage in router.go:
//
//	datasets := authorized.Group("/datasets")
//	datasets.GET("",  middleware.RequirePermission(common.PermDatasetRead),  h.ListDatasets)
//	datasets.POST("", middleware.RequirePermission(common.PermDatasetCreate), h.CreateDataset)
//
// MUST be registered AFTER AuthMiddleware and WorkspaceMiddleware in the
// chain. Routes that take a workspace-aware action without a permission
// (legacy /healthz etc.) should NOT use this middleware — they belong on
// the apiNoAuth group.
func RequirePermission(perm common.Permission) gin.HandlerFunc {
	rbacDao := dao.NewRBACDao()
	return func(c *gin.Context) {
		userID, ok := c.Get("user_id")
		if !ok {
			deny(c, "authentication required")
			return
		}
		uid, _ := userID.(string)
		if uid == "" {
			deny(c, "authentication required")
			return
		}

		tenantID := c.GetString(WorkspaceTenantKey)
		if tenantID == "" {
			deny(c, "workspace context required")
			return
		}

		ctx, err := loadOrResolveContext(c, rbacDao, uid, tenantID)
		if err != nil {
			common.Warn("rbac: resolve context failed",
				zap.String("user_id", uid),
				zap.String("tenant_id", tenantID),
				zap.Error(err),
			)
			deny(c, "permission check failed")
			return
		}
		if ctx == nil {
			// User does not exist — fail closed.
			deny(c, "permission denied")
			return
		}

		if ctx.IsSuperuser {
			// TODO: port AuditService.record("SUPERUSER_BYPASS") from Python
			// once we have an AuditDao in Go. For now log so the operator at
			// least sees the bypass in the structured log stream.
			common.Info("rbac: superuser bypass",
				zap.String("user_id", uid),
				zap.String("tenant_id", tenantID),
				zap.String("permission", string(perm)),
			)
			c.Next()
			return
		}

		if ctx.WorkspaceID == "" {
			common.Warn("rbac: no workspace for tenant_id",
				zap.String("user_id", uid),
				zap.String("tenant_id", tenantID),
			)
			deny(c, "permission denied")
			return
		}

		if ctx.OrgRole == common.OrgRoleOrgAdmin {
			c.Next()
			return
		}

		if ctx.WsRole == "" {
			deny(c, "permission denied")
			return
		}

		if common.HasRolePermission(ctx.WsRole, perm) {
			c.Next()
			return
		}

		deny(c, "permission denied: "+string(perm))
	}
}

// loadOrResolveContext returns the RBACContext for (userID, tenantID),
// using the per-request cache when available.
func loadOrResolveContext(c *gin.Context, rbacDao *dao.RBACDao, userID, tenantID string) (*dao.RBACContext, error) {
	if cached, ok := c.Get(rbacContextKey); ok {
		if ctx, ok := cached.(*dao.RBACContext); ok {
			return ctx, nil
		}
	}
	ctx, err := rbacDao.ResolveContext(userID, tenantID)
	if err != nil {
		return nil, err
	}
	c.Set(rbacContextKey, ctx)
	return ctx, nil
}

// deny writes the standard 403 body and aborts the handler chain.
func deny(c *gin.Context, message string) {
	c.JSON(http.StatusForbidden, gin.H{
		"code":    http.StatusForbidden,
		"message": message,
	})
	c.Abort()
}
