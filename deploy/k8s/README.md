# Kubernetes Deployment Example

These manifests are a starting point. Before deploying, complete the customization checklist below.

## Prerequisites

- A running Kubernetes cluster (k3s, kubeadm, EKS, GKE, etc.)
- `kubectl` configured and pointed at your cluster
- A container registry with the built images (see `docker/` at the project root)
- Optional: cert-manager + an ingress controller for TLS

## Customization Checklist

### 1. Credentials (secrets.yaml)

Edit `secrets.yaml` and replace all three `CHANGE_ME` values with real passwords:

```yaml
stringData:
  source-password: "your-source-password"
  relay-password:  "your-relay-password"
  admin-password:  "your-admin-password"
```

### 2. Icecast and Liquidsoap configs (configmaps.yaml)

Because icecast and Liquidsoap read raw config files (not env vars), their passwords
cannot be injected automatically from the Secret. Update the `CHANGE_ME` values in
`configmaps.yaml` to match the passwords you set in `secrets.yaml`:

- `icecast.xml`: `<source-password>`, `<relay-password>`, `<admin-password>`
- `radio.liq`: `password=` in the `output.icecast` block
- `config.yaml`: `icecast_password:` (overridden at runtime by `ICECAST_PASSWORD` env var)

### 3. Container images (icecast.yaml, radio.yaml)

Replace `YOUR_REGISTRY` with your container registry path:

```yaml
image: ghcr.io/yourorg/radio-agent-icecast:latest
image: ghcr.io/yourorg/radio-agent-brain:latest
```

Build and push the images first:

```bash
cd docker/
docker build -t YOUR_REGISTRY/radio-agent-icecast:latest -f Dockerfile.icecast .
docker build -t YOUR_REGISTRY/radio-agent-brain:latest -f Dockerfile.brain .
docker push YOUR_REGISTRY/radio-agent-icecast:latest
docker push YOUR_REGISTRY/radio-agent-brain:latest
```

### 4. Node and data paths (radio.yaml)

The radio and liquidsoap containers need access to your music and tones directories.

Option A (hostPath): Pin to the node that has the files:

```yaml
nodeSelector:
  kubernetes.io/hostname: your-node-name  # kubectl get nodes

volumes:
  - name: music
    hostPath:
      path: /path/to/your/music       # absolute path on the node
  - name: tones
    hostPath:
      path: /path/to/your/tones
```

Option B (PersistentVolumeClaim): Create PVCs backed by NFS or a storage class,
then replace the hostPath volumes with `persistentVolumeClaim` references.

### 5. Domain name (ingress.yaml)

Replace `radio.example.com` with your actual domain:

```yaml
rules:
  - host: your-domain.com
tls:
  - hosts:
      - your-domain.com
```

Also update `cert-manager.io/cluster-issuer` to match your cert-manager issuer name.

## Deploy

```bash
kubectl apply -f namespace.yaml
kubectl apply -f secrets.yaml
kubectl apply -f configmaps.yaml
kubectl apply -f icecast.yaml
kubectl apply -f radio.yaml
kubectl apply -f ingress.yaml   # optional
```

## Verify

```bash
kubectl get pods -n radio-agent
kubectl logs -n radio-agent deployment/radio -c brain
kubectl logs -n radio-agent deployment/icecast
```
