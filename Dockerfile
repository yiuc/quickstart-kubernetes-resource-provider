FROM lambci/lambda:build-python3.7

COPY . /build

WORKDIR /build

RUN pip3 install -t get --upgrade -r get/requirements.txt

RUN cd get && mv src/* ./ && rm -rf src && zip -r ../vpc.zip ./ && mv ../vpc.zip ./awsqs_kubernetes_get

RUN cd get && zip -r -q ../inner.zip ./ && mv awsqs-kubernetes-get.json schema.json && zip -r -q ../awsqs_kubernetes_get.zip ../inner.zip ./rpdk-config schema.json

CMD mkdir -p /output/ && mv /build/*.zip /output/
