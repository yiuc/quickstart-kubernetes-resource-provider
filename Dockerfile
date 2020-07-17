FROM lambci/lambda:build-python3.7

ENV VERSION="1.16.8/2020-04-16"

COPY . /build

WORKDIR /build

RUN pip3 install -t get/src --upgrade -r get/requirements.txt && \
    find get/src -name __pycache__ | xargs rm -rf && \
    rm -rf get/src/*.dist-info &&\
    curl -o get/src/bin/kubectl https://amazon-eks.s3-us-west-2.amazonaws.com/${VERSION}/bin/linux/amd64/kubectl && \
    curl -o get/src/bin/aws-iam-authenticator https://amazon-eks.s3-us-west-2.amazonaws.com/${VERSION}/bin/linux/amd64/aws-iam-authenticator && \
    chmod +x get/src/bin/kubectl && \
    chmod +x get/src/bin/aws-iam-authenticator

RUN cd get/src && \
    zip -r ../vpc.zip ./ && \
    cp ../vpc.zip /build/awsqs_kubernetes_get_vpc.zip && \
    mv ../vpc.zip ./awsqs_kubernetes_get

RUN cd get/src && zip -r -q ../ResourceProvider.zip ./ && \
    cd ../ && \
    mv awsqs-kubernetes-get.json schema.json && \
    zip -r -q ../awsqs_kubernetes_get.zip ./ResourceProvider.zip .rpdk-config schema.json

RUN pip3 install -t apply/src --upgrade -r apply/requirements.txt && \
    find apply/src -name __pycache__ | xargs rm -rf && \
    rm -rf apply/src/*.dist-info && \
    cp -p get/src/bin/kubectl apply/src/bin/ && \
    cp -p get/src/bin/aws-iam-authenticator apply/src/bin/

RUN cd apply/src && \
    zip -r ../vpc.zip ./ && \
    cp ../vpc.zip /build/awsqs_kubernetes_apply_vpc.zip && \
    mv ../vpc.zip ./awsqs_kubernetes_resource/

RUN cd apply/src && zip -r -q ../ResourceProvider.zip ./ && \
    cd ../ && \
    mv awsqs-kubernetes-resource.json schema.json && \
    zip -r -q ../awsqs_kubernetes_apply.zip ./ResourceProvider.zip .rpdk-config schema.json

CMD mkdir -p /output/ && mv /build/*.zip /output/
