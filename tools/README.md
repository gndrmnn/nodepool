Run the following command from the `tools` directory. 

1. Generate the required certificates:

```bash
mkdir -p $(pwd)/ca
./zk-ca.sh $(pwd)/ca nodepool-test-zookeeper
```

2. Start the Zookeeper and Nodepool test container:

```bash
docker-compose -f docker-compose-nodepool.yaml up
```

3. Exec into the Nodepool test container and run the test script:

```bash
CONTAINER_ID=$(docker ps -aqf "name=tools-launcher-1")
docker exec -it $CONTAINER_ID bash -c 'python3 /nodepool_source/tools/load-test.py --label ubuntu-test'
```



















```bash
./test-setup-docker.sh
```

docker build -t nodepool-test-container .
docker run -it -v /Users/cmr/workspaceZuul/nodepool:/nodepool nodepool-test-container

```bash
pip install -e .
```





```
nodepool-launcher -f -c /Users/cmr/workspaceZuul/nodepool/tools/nodepool.yaml -p /Users/cmr/workspaceZuul/nodepool/tools/nodepool.pid

```


```yaml
providers:
  - name: ec2-us-west-2
    driver: aws
    region-name: us-west-2
    cloud-images:
      - name: debian9
        image-id: ami-09c308526d9534717
        username: admin
    pools:
      - name: main
        max-servers: 5
        subnet-id: subnet-0123456789abcdef0
        security-group-id: sg-01234567890abcdef
        labels:
          - name: debian9
            cloud-image: debian9
            instance-type: t3.medium
            iam-instance-profile:
              arn: arn:aws:iam::123456789012:instance-profile/s3-read-only
            key-name: zuul
            tags:
              key1: value1
          - name: debian9-large
            cloud-image: debian9
            instance-type: t3.large
            key-name: zuul
            use-spot: True
            tags:
              key1: value1
              key2: value2
```

```bash
docker stop <CONTAINER-ID>
```

