//
//  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
//
//  Licensed under the Apache License, Version 2.0 (the "License");
//  you may not use this file except in compliance with the License.
//  You may obtain a copy of the License at
//
//      http://www.apache.org/licenses/LICENSE-2.0
//
//  Unless required by applicable law or agreed to in writing, software
//  distributed under the License is distributed on an "AS IS" BASIS,
//  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
//  See the License for the specific language governing permissions and
//  limitations under the License.
//

package handler

import (
	"ragflow/internal/common"
	"ragflow/internal/entity"

	"github.com/gin-gonic/gin"
)

func GetUser(c *gin.Context) (*entity.User, common.ErrorCode, string) {
	userAny, exist := c.Get("user")
	if !exist {
		return nil, common.CodeUnauthorized, "User not found"
	}

	user, ok := userAny.(*entity.User)
	if !ok {
		return nil, common.CodeUnauthorized, "User not found"
	}
	return user, common.CodeSuccess, ""
}

// GetTenantID returns the active tenant_id for the current request.
// The value is set by WorkspaceMiddleware after validating X-Workspace-Id.
// X-Workspace-Id is mandatory — every user belongs to at least one workspace.
// Returns "" only if the middleware was bypassed (should not happen in practice).
func GetTenantID(c *gin.Context) string {
	if tid, exists := c.Get("tenant_id"); exists {
		if s, ok := tid.(string); ok {
			return s
		}
	}
	return ""
}
