package cmd

import (
	"slices"
	"testing"
)

// A service scaled to several replicas is several containers under one name.
// Each name the caller gets back costs one Docker log read bounded by the
// status timeout, and the caller keys the results by service, so a repeated
// name buys latency and overwrites its own answer.
func TestFailingServicesNamesEachServiceOnce(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name       string
		containers []containerInfo
		want       []string
	}{
		{
			name: "a scaled service is named once",
			containers: []containerInfo{
				{Service: "worker", State: "restarting"},
				{Service: "worker", State: "restarting"},
				{Service: "worker", State: "exited"},
			},
			want: []string{"worker"},
		},
		{
			name: "healthy replicas do not mask a failing sibling",
			containers: []containerInfo{
				{Service: "worker", State: "running"},
				{Service: "worker", State: "exited"},
				{Service: "backend", Health: "unhealthy"},
			},
			want: []string{"worker", "backend"},
		},
		{
			name: "first-seen order is kept",
			containers: []containerInfo{
				{Service: "backend", State: "restarting"},
				{Service: "postgres", Health: "unhealthy"},
				{Service: "backend", Health: "unhealthy"},
			},
			want: []string{"backend", "postgres"},
		},
		{
			name: "a healthy stack names nothing",
			containers: []containerInfo{
				{Service: "backend", State: "running"},
				{Service: "postgres", State: "running", Health: "healthy"},
			},
			want: nil,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			if got := failingServices(tt.containers); !slices.Equal(got, tt.want) {
				t.Errorf("failingServices() = %v, want %v", got, tt.want)
			}
		})
	}
}
