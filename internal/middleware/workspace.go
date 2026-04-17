// Package middleware provides custom middleware for the RAGFlow Go server.
// This file is part of the multi-tenant B2B SaaS layer and is NOT upstream code.
//
// Every user belongs to at least one workspace (the "général" default workspace
// created at org onboarding). There is no personal-tenant fallback: X-Workspace-Id
// is REQUIRED on every authenticated request. Missing header → 403.
//
// Merge note: this file has no upstream equivalent — it will never conflict.
// The only upstream touch-point is the single authorized.Use() line in router.go.
package middleware

import (
	"net/http"
	"ragflow/internal/dao"
	"ragflow/internal/entity"
	"ragflow/internal/logger"

	"github.com/gin-gonic/gin"
	"go.uber.org/zap"
)

const WorkspaceTenantKey = "tenant_id"
const WorkspaceIDKey = "active_workspace_id"

// WorkspaceMiddleware resolves the active tenant for every authenticated request.
//
// Rules:
//  1. No X-Workspace-Id header → 403 (every user must belong to a workspace).
//  2. X-Workspace-Id present:
//     a. Workspace must exist and be active (status=1).
//     b. User must be a WsMember  → use workspace.tenant_id.
//     c. OR user is superuser     → use workspace.tenant_id (logged).
//     d. OR user is org_admin of the workspace's parent org → use workspace.tenant_id (logged).
//     e. Otherwise → 403 Forbidden.
type WorkspaceMiddleware struct {
	wsDAO *dao.WorkspaceDAO
}

func NewWorkspaceMiddleware() *WorkspaceMiddleware {
	return &WorkspaceMiddleware{wsDAO: dao.NewWorkspaceDAO()}
}

func (m *WorkspaceMiddleware) Resolve() gin.HandlerFunc {
	return func(c *gin.Context) {
		wsID := c.GetHeader("X-Workspace-Id")
		if wsID == "" {
			// Every user must operate within a workspace — no personal-tenant fallback.
			c.JSON(http.StatusForbidden, gin.H{
				"code":    403,
				"message": "X-Workspace-Id header is required",
			})
			c.Abort()
			return
		}

		// Retrieve authenticated user (set by AuthMiddleware).
		userAny, exists := c.Get("user")
		if !exists {
			c.JSON(http.StatusUnauthorized, gin.H{"code": 401, "message": "Unauthorized"})
			c.Abort()
			return
		}
		user, ok := userAny.(*entity.User)
		if !ok {
			c.JSON(http.StatusUnauthorized, gin.H{"code": 401, "message": "Unauthorized"})
			c.Abort()
			return
		}

		// Fetch workspace and verify it is active.
		ws, err := m.wsDAO.GetActiveByID(wsID)
		if err != nil {
			c.JSON(http.StatusForbidden, gin.H{
				"code":    403,
				"message": "Workspace not found or inactive",
			})
			c.Abort()
			return
		}

		// Check membership.
		if m.wsDAO.IsMember(wsID, user.ID) {
			c.Set(WorkspaceTenantKey, ws.TenantID)
			c.Set(WorkspaceIDKey, wsID)
			c.Next()
			return
		}

		// Superuser bypass.
		if user.IsSuperuser != nil && *user.IsSuperuser {
			logger.Info("RBAC superuser bypass (Go)",
				zap.String("user_id", user.ID),
				zap.String("workspace_id", wsID),
				zap.String("tenant_id", ws.TenantID),
			)
			c.Set(WorkspaceTenantKey, ws.TenantID)
			c.Set(WorkspaceIDKey, wsID)
			c.Next()
			return
		}

		// Org-admin bypass.
		if m.wsDAO.IsOrgAdmin(ws.OrgID, user.ID) {
			logger.Info("RBAC org_admin bypass (Go)",
				zap.String("user_id", user.ID),
				zap.String("org_id", ws.OrgID),
				zap.String("workspace_id", wsID),
				zap.String("tenant_id", ws.TenantID),
			)
			c.Set(WorkspaceTenantKey, ws.TenantID)
			c.Set(WorkspaceIDKey, wsID)
			c.Next()
			return
		}

		// Not a member — deny.
		c.JSON(http.StatusForbidden, gin.H{
			"code":    403,
			"message": "You are not a member of this workspace",
		})
		c.Abort()
	}
}
