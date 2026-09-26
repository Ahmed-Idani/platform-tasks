// Package storage is the API's access to S3-compatible object storage: Garage locally,
// AWS S3 in prod. Same code path for both; only configuration differs:
//
//	S3_ENDPOINT_URL   set for Garage (http://localhost:3900), unset for AWS S3
//	S3_BUCKET         platform-tasks-dev / platform-tasks-prod
//	AWS_REGION        "garage" locally, the real region in prod
//	AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY locally; IRSA (no keys) on EKS
//
// Credentials go through the SDK's default chain, so nothing here knows which
// environment it runs in.
package storage

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"strings"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/aws/aws-sdk-go-v2/service/s3/types"
)

var ErrNotFound = errors.New("object not found")

type Storage struct {
	client *s3.Client
	bucket string
}

func New(ctx context.Context) (*Storage, error) {
	bucket := os.Getenv("S3_BUCKET")
	if bucket == "" {
		return nil, errors.New("S3_BUCKET is not set")
	}
	cfg, err := config.LoadDefaultConfig(ctx)
	if err != nil {
		return nil, fmt.Errorf("aws config: %w", err)
	}
	endpoint := os.Getenv("S3_ENDPOINT_URL")
	client := s3.NewFromConfig(cfg, func(o *s3.Options) {
		if endpoint != "" {
			o.BaseEndpoint = aws.String(endpoint)
			o.UsePathStyle = true // http://host/bucket/key: no wildcard DNS needed locally
		}
	})
	return &Storage{client: client, bucket: bucket}, nil
}

func (s *Storage) Bucket() string { return s.bucket }

// URI returns the s3:// form stored in Postgres, e.g. s3://platform-tasks-dev/webhooks/{id}.json
func (s *Storage) URI(key string) string { return "s3://" + s.bucket + "/" + key }

// keyOf accepts an s3:// URI of this bucket and returns its key.
func (s *Storage) keyOf(uri string) (string, error) {
	prefix := "s3://" + s.bucket + "/"
	if !strings.HasPrefix(uri, prefix) {
		return "", fmt.Errorf("%q is not in bucket %s", uri, s.bucket)
	}
	return strings.TrimPrefix(uri, prefix), nil
}

func (s *Storage) Get(ctx context.Context, uri string) ([]byte, error) {
	key, err := s.keyOf(uri)
	if err != nil {
		return nil, err
	}
	out, err := s.client.GetObject(ctx, &s3.GetObjectInput{Bucket: &s.bucket, Key: &key})
	if err != nil {
		var nsk *types.NoSuchKey
		if errors.As(err, &nsk) {
			return nil, ErrNotFound
		}
		return nil, fmt.Errorf("get %s: %w", uri, err)
	}
	defer out.Body.Close()
	return io.ReadAll(out.Body)
}

func (s *Storage) Delete(ctx context.Context, uri string) error {
	key, err := s.keyOf(uri)
	if err != nil {
		return err
	}
	if _, err := s.client.DeleteObject(ctx, &s3.DeleteObjectInput{Bucket: &s.bucket, Key: &key}); err != nil {
		return fmt.Errorf("delete %s: %w", uri, err)
	}
	return nil
}

// ApplyLifecycle installs the bucket's expiry rules. Idempotent (PUT replaces the
// whole configuration). Called by cmd/storage-init, not by the API at runtime:
// bucket configuration is infrastructure, like the RabbitMQ topology.
func (s *Storage) ApplyLifecycle(ctx context.Context, rules map[string]int32) error {
	var lr []types.LifecycleRule
	for prefix, days := range rules {
		lr = append(lr, types.LifecycleRule{
			ID:         aws.String("expire-" + strings.TrimSuffix(prefix, "/")),
			Status:     types.ExpirationStatusEnabled,
			Filter:     &types.LifecycleRuleFilter{Prefix: aws.String(prefix)},
			Expiration: &types.LifecycleExpiration{Days: aws.Int32(days)},
		})
	}
	_, err := s.client.PutBucketLifecycleConfiguration(ctx, &s3.PutBucketLifecycleConfigurationInput{
		Bucket:                 &s.bucket,
		LifecycleConfiguration: &types.BucketLifecycleConfiguration{Rules: lr},
	})
	return err
}
