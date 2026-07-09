# image-translation-server

A Python Flask server providing APIs for calling [ImageTrans](https://www.basiccat.org/imagetrans/) to translate images like manga, comics, manhua, webtoon, etc.

You need to zip ImageTrans files and upload via the web interface or through the API.

Its API is compatible with the [ImageTrans_wsServer](https://github.com/xulihang/ImageTrans_wsServer) project. It can be used for <https://github.com/xulihang/ImageTrans_chrome_extension>

## Docker

Docker image is also available: <https://hub.docker.com/r/xulihang/imagetrans>

Run the following to start it:

1. docker pull xulihang/imagetrans
2. docker run -d -p 5000:5000 --name imagetrans xulihang/imagetrans
3. Go to http://localhost:5000 and upload ImageTrans.zip via the web interface.

## ZIP

The zip should contain the root level of ImageTrans, with files like the following:

```
jre
ImageTrans.jar
```

## Supported Platforms

* Windows
* macOS
* Linux

  
