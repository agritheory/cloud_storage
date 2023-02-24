# [Amazon Web Services S3 Setup](https://aws.amazon.com/s3/)

## Regions

The following documentation shows the available region names for AWS:

- [Amazon Web Services S3 Regions](https://docs.aws.amazon.com/general/latest/gr/s3.html)

## Credentials

In order to generate access keys for your Amazon Web Services S3 instance, go through the following steps:

- Open the [IAM console](https://console.aws.amazon.com/iam/home#home).
- From the navigation menu, click on **Users**.
- Select your IAM user name.
- Scroll to the **Access keys** section and click on **Create access key**.
  - In **Step 1**, select **Third-party service**. Confirm and select **Next**.
  - In **Step 2**, optionally set a description for the access key.
  - In **Step 3**, click on **Download .csv file**. Your keys should look something like this:
    - Access key: AKIAIOSFODNN7EXAMPLE
    - Secret access key: MihgBbJs5yQrKsKNSj/TEqa3q2ZOtaEXAMPLEKEY
- Once you have the access key and secret access key, store them in a secure location.

## Buckets

The following documentation shows how to generate buckets in Amazon's S3 service:

- [Amazon Web Services S3 - Create a Bucket](https://docs.aws.amazon.com/AmazonS3/latest/userguide/creating-bucket.html)
